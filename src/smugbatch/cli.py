"""CLI entry point for smugbatch."""

import click
import yaml

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from .api import (create_gallery, delete_album_image, find_existing_gallery,
                  get_album_from_node, get_album_images, patch_album,
                  resolve_folder, resolve_gallery_url, sort_album_images,
                  _get_oauth_session)
from .auth import check_auth, run_oauth_flow
from .config import load_config
from .smartrules import apply_smart_rules, build_recipe, get_numeric_album_id, has_rules


@click.group()
def cli():
    """Batch create SmugMug galleries with smart rules."""


@cli.command()
@click.option("--check", is_flag=True, help="Validate stored credentials.")
def auth(check):
    """Authenticate with SmugMug (OAuth 1.0a)."""
    if check:
        check_auth()
    else:
        run_oauth_flow()


@cli.command()
@click.argument("batch_file", type=click.Path(exists=True))
@click.option("--limit", type=int, default=None, help="Only process the first N galleries.")
@click.option("--force-settings", is_flag=True, help="Re-apply all gallery settings (including passwords).")
def batch(batch_file, limit, force_settings):
    """Create galleries from a batch YAML file."""
    with open(batch_file) as f:
        spec = yaml.safe_load(f)

    config = load_config()
    nickname = config["user"]["nickname"]
    smsess = config["session"]["smsess"]

    if not smsess:
        raise SystemExit("Session cookie (smsess) not set in config. Needed for smart rules.")

    session = _get_oauth_session(config)

    parent_folder = spec["parent_folder"]
    privacy = spec.get("privacy", "Unlisted")
    common_keywords = spec.get("common_keywords", [])
    date_start = spec["date_start"]
    date_stop = spec["date_stop"]
    use_unlisted = spec.get("use_unlisted", True)
    match = spec.get("match", "All")
    galleries = spec["galleries"]
    gallery_settings = spec.get("gallery_settings", {})

    if limit is not None:
        galleries = galleries[:limit]
        click.echo(f"Limiting to first {limit} gallery(s).")

    # Resolve parent folder once
    click.echo(f"Resolving folder: {parent_folder}")
    parent_node_uri = resolve_folder(session, nickname, parent_folder)
    click.echo(f"  → {parent_node_uri}")

    created = 0
    updated = 0
    skipped = 0
    total = len(galleries)
    for i, gallery_name in enumerate(galleries, 1):
        url_name = gallery_name.replace(" ", "-")
        click.echo(f"\n[{i}/{total}] {gallery_name}")

        # Check if gallery already exists
        existing = find_existing_gallery(session, parent_node_uri, url_name)
        if existing:
            node_uri = existing["Uri"]
            album = get_album_from_node(session, node_uri)
            album_key = album["AlbumKey"]
            album_id = get_numeric_album_id(album_key, smsess)
            already_has_rules = has_rules(album_id, album_key, smsess)

            # Check if settings need updating (skip password fields — API masks them)
            SKIP_COMPARE = {"Password", "DownloadPassword"}
            needs_settings = False
            if gallery_settings:
                for k, v in gallery_settings.items():
                    if k in SKIP_COMPARE:
                        continue
                    if album.get(k) != v:
                        needs_settings = True
                        break

            if force_settings and gallery_settings:
                needs_settings = True

            if already_has_rules and not needs_settings:
                click.echo(f"  Up to date, skipping.")
                skipped += 1
                continue

            click.echo(f"  Exists, updating...")
        else:
            # Create gallery
            result = create_gallery(session, parent_node_uri, gallery_name, url_name, privacy)
            node = result["Response"]["Node"]
            node_uri = node["Uri"]
            click.echo(f"  Created node: {node_uri}")

            album = get_album_from_node(session, node_uri)
            album_key = album["AlbumKey"]
            album_id = get_numeric_album_id(album_key, smsess)
            already_has_rules = False
            needs_settings = bool(gallery_settings)
            created += 1

        click.echo(f"  Album: {album_id} / {album_key}")

        # Apply gallery settings if needed
        if needs_settings:
            patch_album(session, album_key, gallery_settings)
            click.echo(f"  Settings applied.")

        # Apply smart rules if needed
        if not already_has_rules:
            recipe = build_recipe(
                gallery_keywords=[gallery_name],
                common_keywords=common_keywords,
                date_start=date_start,
                date_stop=date_stop,
                nickname=nickname,
                use_unlisted=use_unlisted,
                match=match,
            )
            apply_smart_rules(album_id, album_key, recipe, smsess)
            click.echo(f"  Smart rules applied.")

        if existing:
            updated += 1

    click.echo(f"\nDone! Created {created}, updated {updated}, skipped {skipped} (of {total}).")


@cli.command()
@click.argument("gallery")
@click.option("--delete", is_flag=True, help="Delete duplicates (default: dry run).")
@click.option("--keep-latest", is_flag=True, help="Keep the latest upload instead of earliest.")
@click.option("--force", is_flag=True, help="Also delete same-name different-content duplicates.")
@click.option("--parallel", type=int, default=4, show_default=True, help="Number of parallel delete requests.")
def dupes(gallery, delete, keep_latest, force, parallel):
    """Find (and optionally remove) duplicate images in a gallery.

    GALLERY can be a SmugMug URL (e.g. https://...com/.../n-LgKGwb) or a bare AlbumKey.
    """
    config = load_config()
    nickname = config["user"]["nickname"]
    session = _get_oauth_session(config)

    click.echo("Resolving gallery...")
    album_key = resolve_gallery_url(session, gallery, nickname=nickname)
    click.echo(f"Album: {album_key}")

    click.echo("Fetching images...", nl=False)
    images = get_album_images(session, album_key)
    click.echo(f" {len(images)} found.")

    # Group by filename
    by_name = defaultdict(list)
    for img in images:
        by_name[img["FileName"]].append(img)

    # Find duplicates
    identical_to_delete = []
    different_to_delete = []
    identical_count = 0
    different_count = 0

    reverse = keep_latest  # sort ascending by default (keep earliest), reverse to keep latest

    for fname, copies in sorted(by_name.items()):
        if len(copies) < 2:
            continue

        # Group by MD5 within same filename
        by_md5 = defaultdict(list)
        for img in copies:
            by_md5[img["ArchivedMD5"]].append(img)

        if len(by_md5) == 1:
            # All copies identical
            identical_count += 1
            sorted_copies = sorted(copies, key=lambda x: x["DateTimeUploaded"], reverse=reverse)
            keep = sorted_copies[0]
            extras = sorted_copies[1:]
            click.echo(f"\n  {fname} ({len(copies)} copies, identical)")
            click.echo(f"    Keep:   {keep['ImageKey']}  uploaded {keep['DateTimeUploaded']}")
            for img in extras:
                click.echo(f"    Remove: {img['ImageKey']}  uploaded {img['DateTimeUploaded']}")
            identical_to_delete.extend(extras)
        else:
            # Mixed MD5s
            different_count += 1
            click.echo(f"\n  {fname} ({len(copies)} copies, {len(by_md5)} distinct versions)")
            for md5, group in by_md5.items():
                sorted_group = sorted(group, key=lambda x: x["DateTimeUploaded"], reverse=reverse)
                if len(group) > 1:
                    # Identical copies within this MD5 group
                    click.echo(f"    MD5 {md5[:8]}...:")
                    click.echo(f"      Keep:   {sorted_group[0]['ImageKey']}  uploaded {sorted_group[0]['DateTimeUploaded']}")
                    for img in sorted_group[1:]:
                        click.echo(f"      Remove: {img['ImageKey']}  uploaded {img['DateTimeUploaded']}")
                    identical_to_delete.extend(sorted_group[1:])
                else:
                    label = "Remove:" if force else "Skip:  "
                    click.echo(f"    MD5 {md5[:8]}...: {label} {group[0]['ImageKey']}  uploaded {group[0]['DateTimeUploaded']}")
                    if force:
                        # In force mode, keep only the one from the largest MD5 group or first sorted
                        different_to_delete.extend(group)

    # In force mode, for different-content groups we kept all in different_to_delete;
    # now remove the one we want to keep (earliest/latest overall per filename)
    if force and different_to_delete:
        # Re-process: group different_to_delete by filename, keep one per filename
        force_by_name = defaultdict(list)
        for img in different_to_delete:
            force_by_name[img["FileName"]].append(img)
        different_to_delete = []
        for fname, group in force_by_name.items():
            sorted_group = sorted(group, key=lambda x: x["DateTimeUploaded"], reverse=reverse)
            different_to_delete.extend(sorted_group[1:])

    to_delete = identical_to_delete + different_to_delete

    if not identical_count and not different_count:
        click.echo("\nNo duplicates found.")
        return

    click.echo(f"\n{identical_count + different_count} duplicate filenames found "
               f"({identical_count} identical, {different_count} different content).")

    if not delete:
        click.echo(f"{len(to_delete)} images would be removed. Run with --delete to remove them.")
        return

    if not to_delete:
        click.echo("Nothing to delete.")
        return

    click.confirm(f"Delete {len(to_delete)} duplicate images?", abort=True)

    deleted = 0
    errors = 0

    def _do_delete(img):
        # Each thread needs its own OAuth session (requests.Session is not thread-safe)
        thread_session = _get_oauth_session(config)
        delete_album_image(thread_session, album_key, img["ImageKey"])
        return img

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        futures = {pool.submit(_do_delete, img): img for img in to_delete}
        for future in as_completed(futures):
            img = futures[future]
            try:
                future.result()
                deleted += 1
                click.echo(f"  [{deleted + errors}/{len(to_delete)}] Deleted {img['ImageKey']} ({img['FileName']})")
            except Exception as e:
                errors += 1
                click.echo(f"  [{deleted + errors}/{len(to_delete)}] FAILED {img['ImageKey']} ({img['FileName']}): {e}")

    click.echo(f"\nDeleted {deleted} images ({errors} errors). {len(images) - deleted} remain.")


@cli.command()
@click.argument("gallery")
@click.option("--by", "sort_by", type=click.Choice(["day"]), required=True,
              help="Sort strategy: 'day' = newest day first, chronological within each day.")
def sort(gallery, sort_by):
    """Sort images in a gallery.

    GALLERY can be a SmugMug URL (e.g. https://...com/.../n-LgKGwb) or a bare AlbumKey.
    """
    config = load_config()
    nickname = config["user"]["nickname"]
    session = _get_oauth_session(config)

    click.echo("Resolving gallery...")
    album_key = resolve_gallery_url(session, gallery, nickname=nickname)
    click.echo(f"Album: {album_key}")

    click.echo("Fetching images...", nl=False)
    images = get_album_images(session, album_key)
    click.echo(f" {len(images)} found.")

    if len(images) < 2:
        click.echo("Nothing to sort.")
        return

    # Build desired order: days descending, chronological within each day
    by_day = defaultdict(list)
    for img in images:
        by_day[img["DateTimeOriginal"][:10]].append(img)

    desired_order = []
    for day in sorted(by_day.keys(), reverse=True):
        desired_order.extend(sorted(by_day[day], key=lambda x: x["DateTimeOriginal"]))

    # Check if already sorted
    current_keys = [img["ImageKey"] for img in images]
    desired_keys = [img["ImageKey"] for img in desired_order]

    if current_keys == desired_keys:
        click.echo("Already in order, nothing to do.")
        return

    # Show the day breakdown
    days = sorted(by_day.keys(), reverse=True)
    click.echo(f"\nSorting {len(images)} images across {len(days)} days:")
    for day in days:
        click.echo(f"  {day}: {len(by_day[day])} images")

    # Ensure SortMethod is Position
    album_data = session.get(f"https://api.smugmug.com/api/v2/album/{album_key}",
                             headers={"Accept": "application/json"}).json()
    album = album_data["Response"]["Album"]
    if album.get("SortMethod") != "Position":
        click.echo(f"\nChanging sort method from '{album.get('SortMethod')}' to 'Position'...")
        patch_album(session, album_key, {"SortMethod": "Position"})

    # Sort: place all images after the first in desired order
    click.echo("Applying sort order...", nl=False)
    first_uri = desired_order[0]["Uri"]
    rest_uris = [img["Uri"] for img in desired_order[1:]]
    sort_album_images(session, album_key, rest_uris, first_uri)
    click.echo(" done.")

    click.echo(f"\nSorted! Newest day ({days[0]}) first, oldest day ({days[-1]}) last.")


@cli.command()
@click.option("--folder", required=True, help="Parent folder path (e.g. /Other/2026-Bahamas).")
@click.option("--name", required=True, help="Gallery display name.")
@click.option("--keyword", "keywords", multiple=True, required=True, help="Keyword filter (repeatable).")
@click.option("--album", "--gallery", "source_album", default=None, help="Source album/gallery URL or AlbumKey (only match photos from this album).")
@click.option("--date-start", default=None, help="Start date for date filter (MM/DD/YYYY).")
@click.option("--date-stop", default=None, help="End date for date filter (MM/DD/YYYY).")
@click.option("--privacy", type=click.Choice(["Public", "Unlisted", "Private"]), default="Unlisted",
              show_default=True, help="Gallery privacy.")
@click.option("--match", type=click.Choice(["All", "Any"]), default="All",
              show_default=True, help="How keywords are combined.")
@click.option("--max-photos", type=int, default=1000, show_default=True, help="Max photos in gallery.")
@click.option("--unlisted/--no-unlisted", default=True, show_default=True,
              help="Include unlisted galleries in smart rule search.")
def rules(folder, name, keywords, source_album, date_start, date_stop, privacy, match, max_photos, unlisted):
    """Create or update a gallery with smart rules.

    Creates the gallery if it doesn't exist, then applies keyword-based smart rules.
    If the gallery already exists, its smart rules are replaced.
    """
    config = load_config()
    nickname = config["user"]["nickname"]
    smsess = config["session"]["smsess"]

    if not smsess:
        raise SystemExit("Session cookie (smsess) not set in config. Needed for smart rules.")

    session = _get_oauth_session(config)

    # Resolve parent folder
    click.echo(f"Resolving folder: {folder}")
    parent_node_uri = resolve_folder(session, nickname, folder)

    # Check if gallery exists
    url_name = name.replace(" ", "-")
    existing = find_existing_gallery(session, parent_node_uri, url_name)

    if existing:
        click.echo(f"Gallery '{name}' exists, replacing rules...")
        node_uri = existing["Uri"]
    else:
        click.echo(f"Creating gallery '{name}'...")
        result = create_gallery(session, parent_node_uri, name, url_name, privacy)
        node_uri = result["Response"]["Node"]["Uri"]
        click.echo(f"  Created.")

    album = get_album_from_node(session, node_uri)
    album_key = album["AlbumKey"]
    album_id = get_numeric_album_id(album_key, smsess)
    click.echo(f"  Album: {album_id} / {album_key}")

    # Resolve source album if specified
    source_album_id = None
    source_album_key = None
    if source_album:
        source_album_key = resolve_gallery_url(session, source_album, nickname=nickname)
        source_album_id = get_numeric_album_id(source_album_key, smsess)
        click.echo(f"  Source album: {source_album_key} ({source_album_id})")

    # Build and apply smart rules
    recipe = build_recipe(
        gallery_keywords=list(keywords),
        common_keywords=[],
        nickname=nickname,
        date_start=date_start,
        date_stop=date_stop,
        source_album_id=source_album_id,
        source_album_key=source_album_key,
        use_unlisted=unlisted,
        match=match,
        max_photos=max_photos,
    )
    apply_smart_rules(album_id, album_key, recipe, smsess)
    click.echo(f"  Smart rules applied: {', '.join(keywords)}")

    # Report result
    from .api import _api_get
    album_data = _api_get(session, f"/api/v2/album/{album_key}")
    image_count = album_data["Response"]["Album"].get("ImageCount", 0)
    click.echo(f"\nDone! Gallery has {image_count} matching images.")
