"""SmugMug API v2 client — folder resolution & gallery creation."""

import re
import time

import click
from requests_oauthlib import OAuth1Session

from .config import load_config

API_BASE = "https://api.smugmug.com"


def _get_oauth_session(config: dict) -> OAuth1Session:
    return OAuth1Session(
        config["oauth"]["consumer_key"],
        client_secret=config["oauth"]["consumer_secret"],
        resource_owner_key=config["oauth"]["access_token"],
        resource_owner_secret=config["oauth"]["access_token_secret"],
    )


def _handle_rate_limit(resp) -> bool:
    """If response is 429, wait and return True (caller should retry). Otherwise return False."""
    if resp.status_code == 429:
        retry_after = int(resp.headers.get("Retry-After", 5))
        click.echo(f"  Rate limited, waiting {retry_after}s...")
        time.sleep(retry_after)
        return True
    return False


def _api_get(session: OAuth1Session, uri: str) -> dict:
    url = f"{API_BASE}{uri}" if uri.startswith("/") else uri
    while True:
        resp = session.get(url, headers={"Accept": "application/json"})
        if not _handle_rate_limit(resp):
            break
    resp.raise_for_status()
    return resp.json()


def resolve_folder(session: OAuth1Session, nickname: str, folder_path: str) -> str:
    """Resolve a folder path like /Dance/Hyline/2026 to a Node ID."""
    parts = [p for p in folder_path.strip("/").split("/") if p]
    # Start from user root node
    user = _api_get(session, f"/api/v2/user/{nickname}")
    node_uri = user["Response"]["User"]["Uris"]["Node"]["Uri"]

    for part in parts:
        children_uri = f"{node_uri}!children"
        # Page through children to find the matching folder
        found = False
        start = 1
        while True:
            data = _api_get(session, f"{children_uri}?start={start}&count=100")
            nodes = data["Response"].get("Node", [])
            if not nodes:
                break
            for node in nodes:
                if node["UrlName"].lower() == part.lower() or node["Name"].lower() == part.lower():
                    node_uri = node["Uri"]
                    found = True
                    break
            if found:
                break
            pages = data["Response"].get("Pages", {})
            if start + 100 > pages.get("Total", 0):
                break
            start += 100

        if not found:
            raise SystemExit(f"Folder not found: '{part}' in path '{folder_path}'")

    return node_uri


def find_existing_gallery(session: OAuth1Session, parent_node_uri: str,
                          url_name: str) -> dict | None:
    """Check if a gallery with this UrlName already exists under parent. Returns node dict or None."""
    children_uri = f"{parent_node_uri}!children"
    start = 1
    while True:
        data = _api_get(session, f"{children_uri}?start={start}&count=100")
        nodes = data["Response"].get("Node", [])
        if not nodes:
            break
        for node in nodes:
            if node["UrlName"].lower() == url_name.lower() and node["Type"] == "Album":
                return node
        pages = data["Response"].get("Pages", {})
        if start + 100 > pages.get("Total", 0):
            break
        start += 100
    return None


def create_gallery(session: OAuth1Session, parent_node_uri: str, name: str,
                   url_name: str, privacy: str) -> dict:
    """Create a gallery node under parent. Returns the created Node response."""
    children_url = f"{API_BASE}{parent_node_uri}!children"
    payload = {
        "Type": "Album",
        "Name": name,
        "UrlName": url_name,
        "Privacy": privacy,
    }
    while True:
        resp = session.post(children_url, json=payload, headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        if not _handle_rate_limit(resp):
            break
    resp.raise_for_status()
    return resp.json()


def get_album_from_node(session: OAuth1Session, node_uri: str) -> dict:
    """Given a node URI, fetch the associated Album (for AlbumID and AlbumKey)."""
    node_data = _api_get(session, node_uri)
    node = node_data["Response"]["Node"]
    if "Album" in node.get("Uris", {}):
        album_uri = node["Uris"]["Album"]["Uri"]
        album_data = _api_get(session, album_uri)
        return album_data["Response"]["Album"]
    raise SystemExit(f"No album found for node: {node_uri}")


def patch_album(session: OAuth1Session, album_key: str, settings: dict) -> dict:
    """PATCH album settings. settings keys are SmugMug API field names."""
    url = f"{API_BASE}/api/v2/album/{album_key}"
    while True:
        resp = session.patch(url, json=settings, headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        if not _handle_rate_limit(resp):
            break
    resp.raise_for_status()
    return resp.json()["Response"]["Album"]


def resolve_gallery_url(session: OAuth1Session, identifier: str, nickname: str = None) -> str:
    """Resolve a SmugMug gallery URL or bare album key to an AlbumKey.

    Accepts:
      - URL with node key: https://...com/.../n-LgKGwb
      - URL with path:     https://...com/School/Dance/2025-Star-Steppers
      - Bare album key:    FjvCpr
    """
    # URL with node key (e.g. /n-LgKGwb)
    match = re.search(r"/n-([A-Za-z0-9]+)", identifier)
    if match:
        node_key = match.group(1)
        album = get_album_from_node(session, f"/api/v2/node/{node_key}")
        return album["AlbumKey"]

    # URL with path (contains :// or /)
    if "://" in identifier:
        from urllib.parse import urlparse
        path = urlparse(identifier).path.strip("/")
        if path and nickname:
            node_uri = resolve_folder(session, nickname, path)
            album = get_album_from_node(session, node_uri)
            return album["AlbumKey"]

    # Treat as bare album key
    return identifier


def get_album_images(session: OAuth1Session, album_key: str) -> list[dict]:
    """Fetch all images in an album (paginated)."""
    all_images = []
    start = 1
    while True:
        data = _api_get(session, f"/api/v2/album/{album_key}!images?count=100&start={start}")
        images = data["Response"].get("AlbumImage", [])
        if not images:
            break
        all_images.extend(images)
        total = data["Response"].get("Pages", {}).get("Total", 0)
        if start + 100 > total:
            break
        start += 100
    return all_images


def delete_album_image(session: OAuth1Session, album_key: str, image_key: str) -> None:
    """Delete an image from an album."""
    url = f"{API_BASE}/api/v2/album/{album_key}/image/{image_key}"
    while True:
        resp = session.delete(url, headers={"Accept": "application/json"})
        if not _handle_rate_limit(resp):
            break
    resp.raise_for_status()


def sort_album_images(session: OAuth1Session, album_key: str,
                      move_uris: list[str], target_uri: str,
                      location: str = "After") -> None:
    """Reorder images in a manually-sorted album.

    Moves all images in move_uris to Before/After the target_uri image.
    """
    url = f"{API_BASE}/api/v2/album/{album_key}!sortimages"
    while True:
        resp = session.post(url, json={
            "MoveUris": ",".join(move_uris),
            "MoveLocation": location,
            "Uri": target_uri,
        }, headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        if not _handle_rate_limit(resp):
            break
    resp.raise_for_status()
