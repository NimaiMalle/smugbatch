"""CLI entry point for smugbatch."""

import click
import yaml

from .api import create_gallery, find_existing_gallery, get_album_from_node, patch_album, resolve_folder, _get_oauth_session
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
