"""Apply smart rules via SmugMug's internal endpoints."""

import json
import time

import click
import requests

SAVE_URL = "https://www.smugmug.com/rpc/gallery.mg"
RPC_URL = "https://www.smugmug.com/services/api/json/1.4.0/"


def get_numeric_album_id(album_key: str, smsess: str) -> int:
    """Get the numeric AlbumID via the legacy RPC API (v2 API doesn't expose it)."""
    resp = requests.get(
        RPC_URL,
        params={"method": "rpc.album.get", "AlbumKey": album_key},
        cookies={"SMSESS": smsess},
        headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("stat") != "ok":
        raise SystemExit(f"Failed to get album info: {data.get('message', data)}")
    return data["Album"]["AlbumID"]


def get_rules(album_id: int, album_key: str, smsess: str) -> dict:
    """Fetch existing smart rules for an album. Returns empty dict if none."""
    resp = requests.get(
        RPC_URL,
        params={"method": "rpc.album.getrules", "AlbumID": album_id, "AlbumKey": album_key},
        cookies={"SMSESS": smsess},
        headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("Rules", {})


def has_rules(album_id: int, album_key: str, smsess: str) -> bool:
    """Check if an album already has smart rules configured."""
    rules = get_rules(album_id, album_key, smsess)
    if not rules:
        return False
    # Rules is either [] (empty) or a dict with Includes
    if isinstance(rules, list):
        return False
    return bool(rules.get("Includes"))


def build_recipe(gallery_keywords: list[str], common_keywords: list[str],
                 nickname: str, date_start: str = None, date_stop: str = None,
                 source_album_id: int = None, source_album_key: str = None,
                 use_unlisted: bool = True, match: str = "All",
                 max_photos: int = 1000) -> dict:
    """Build a smart rules recipe dict."""
    ingredients = []

    for kw in common_keywords + gallery_keywords:
        ingredients.append({
            "Type": "Keyword",
            "word": kw,
            "sort": "Popular",
            "UserNickName": nickname,
            "Operator": "AND",
        })

    if date_start and date_stop:
        ingredients.append({
            "Type": "Date",
            "start": date_start,
            "stop": date_stop,
            "sort": "DateTaken",
            "dateType": "Range",
            "UserNickName": nickname,
            "Operator": "AND",
        })

    if source_album_id and source_album_key:
        ingredients.append({
            "Type": "Gallery",
            "AlbumID": f"{source_album_id}_{source_album_key}",
            "UserNickName": nickname,
            "Operator": "AND",
            "Value": "",
        })

    return {
        "useUnlisted": use_unlisted,
        "maxPhotos": max_photos,
        "match": match,
        "ingredients": ingredients,
    }


def _rpc_gallery_post(tool: str, album_id: int, album_key: str,
                      recipe: dict, smsess: str) -> requests.Response:
    """POST to /rpc/gallery.mg with the given tool name."""
    return requests.post(
        SAVE_URL,
        data={
            "tool": tool,
            "AlbumID": album_id,
            "AlbumKey": album_key,
            "Recipe": json.dumps(recipe),
        },
        cookies={"SMSESS": smsess},
        headers={
            "Accept": "*/*",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": "https://www.smugmug.com",
            "Referer": f"https://www.smugmug.com/gallery/dynamic.mg?AlbumID={album_id}&AlbumKey={album_key}",
        },
    )


def apply_smart_rules(album_id: int, album_key: str, recipe: dict,
                      smsess: str) -> dict:
    """Save smart rules and refresh the gallery to populate matching photos."""
    # Save rules
    resp = _rpc_gallery_post("saveDynamicGallery", album_id, album_key, recipe, smsess)
    resp.raise_for_status()
    data = resp.json()
    if data.get("result") != "success":
        raise SystemExit(f"Failed to save smart rules: {data}")

    # Refresh gallery to populate photos
    resp = _rpc_gallery_post("previewDynamicGallery", album_id, album_key, recipe, smsess)
    resp.raise_for_status()

    return data
