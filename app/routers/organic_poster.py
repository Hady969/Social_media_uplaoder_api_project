# app/routers/organic_poster.py
from __future__ import annotations

import os
import time
import mimetypes
import requests
from typing import List, Optional

from fastapi import APIRouter, HTTPException

from app.models.schemas import OrganicPost, CarouselItem
from app.routers.meta_token_db_reader import MetaTokenDbReader

router = APIRouter()
GRAPH_API_VERSION = "v17.0"

# In-memory list to store organic posts (kept for backward compatibility)
organic_posts: List[OrganicPost] = []

MAX_RETRIES = 20
RETRY_DELAY = 5


def _load_page_access_token_and_ig_user_id(
    client_id: str,
    page_id: str,
    database_url: str,
    fernet_key: str,
) -> tuple[str, str]:
    """
    Returns: (page_access_token, instagram_actor_id)
    """
    reader = MetaTokenDbReader(database_url=database_url, fernet_key=fernet_key)

    page_token_row = reader.get_active_page_token(client_id=client_id, page_id=page_id)
    if isinstance(page_token_row, str):
        page_access_token = page_token_row
    elif isinstance(page_token_row, dict):
        page_access_token = str(page_token_row["access_token"])
    else:
        page_access_token = str(page_token_row.access_token)

    ig_user_id = reader.get_instagram_actor_id_for_client(client_id)
    if not ig_user_id:
        raise HTTPException(
            status_code=400,
            detail=(
                "No Instagram account linked to this client in DB (instagram_account table). "
                "Ensure the selected Page is linked to an IG Business account."
            ),
        )

    return page_access_token, str(ig_user_id)


def _wait_until_media_finished(creation_id: str, page_access_token: str) -> None:
    status_url = (
        f"https://graph.facebook.com/{GRAPH_API_VERSION}/{creation_id}"
        f"?fields=status_code&access_token={page_access_token}"
    )

    for _ in range(MAX_RETRIES):
        status_resp = requests.get(status_url, timeout=60).json()
        if "error" in status_resp:
            raise HTTPException(status_code=400, detail=status_resp["error"])

        status = status_resp.get("status_code")
        if status == "FINISHED":
            return
        if status == "ERROR":
            raise HTTPException(status_code=400, detail="Media failed to process")

        time.sleep(RETRY_DELAY)

    raise HTTPException(status_code=400, detail="Media not ready after multiple attempts")


def _item_type_url(item) -> tuple[str, Optional[str]]:
    """
    Accepts CarouselItem (pydantic model) OR dict.
    Returns: (type, url)
    """
    if hasattr(item, "type"):
        return (item.type or "").strip().lower(), getattr(item, "url", None)
    if isinstance(item, dict):
        return (item.get("type") or "").strip().lower(), item.get("url")
    return "", None


# --------------------- Upload VIDEO (REEL) ---------------------
@router.post("/organic/upload-video-instagram/{organic_post_index}")
def upload_video_instagram(
    client_id: str,
    page_id: str,
    organic_post_index: int,
    database_url: str,
    fernet_key: str,
):
    if organic_post_index >= len(organic_posts):
        raise HTTPException(status_code=404, detail="OrganicPost index out of range")

    post = organic_posts[organic_post_index]

    page_access_token, ig_user_id = _load_page_access_token_and_ig_user_id(
        client_id=client_id,
        page_id=page_id,
        database_url=database_url,
        fernet_key=fernet_key,
    )

    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{ig_user_id}/media"
    data = {
        "media_type": "REELS",
        "video_url": post.video_url,
        "caption": post.title,
        "access_token": page_access_token,
    }

    response = requests.post(url, data=data, timeout=60).json()
    if "error" in response:
        raise HTTPException(status_code=400, detail=response["error"])

    post.creation_id = response["id"]
    return {
        "message": "Instagram video container created",
        "organic_post_index": organic_post_index,
        "creation_id": post.creation_id,
    }


@router.post("/organic/publish-video-instagram/{organic_post_index}")
def publish_video_instagram(
    client_id: str,
    page_id: str,
    organic_post_index: int,
    database_url: str,
    fernet_key: str,
):
    if organic_post_index >= len(organic_posts):
        raise HTTPException(status_code=404, detail="OrganicPost index out of range")

    post = organic_posts[organic_post_index]
    if not post.creation_id:
        raise HTTPException(status_code=400, detail="Creation ID not set for this OrganicPost")

    page_access_token, ig_user_id = _load_page_access_token_and_ig_user_id(
        client_id=client_id,
        page_id=page_id,
        database_url=database_url,
        fernet_key=fernet_key,
    )

    _wait_until_media_finished(post.creation_id, page_access_token)

    publish_url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{ig_user_id}/media_publish"
    data = {
        "creation_id": post.creation_id,
        "access_token": page_access_token,
    }
    publish_resp = requests.post(publish_url, data=data, timeout=60).json()
    if "error" in publish_resp:
        raise HTTPException(status_code=400, detail=publish_resp["error"])

    post.instagram_post_id = publish_resp["id"]
    return {
        "message": "Instagram video published",
        "organic_post_index": organic_post_index,
        "instagram_post_id": post.instagram_post_id,
    }


# --------------------- Upload PHOTO (single image) ---------------------
def upload_photo_instagram(
    client_id: str,
    page_id: str,
    organic_post_index: int,
    database_url: str,
    fernet_key: str,
):
    if organic_post_index >= len(organic_posts):
        raise HTTPException(status_code=404, detail="OrganicPost index out of range")

    post = organic_posts[organic_post_index]
    if not post.image_url:
        raise HTTPException(status_code=400, detail="image_url not set for this OrganicPost")

    page_access_token, ig_user_id = _load_page_access_token_and_ig_user_id(
        client_id=client_id,
        page_id=page_id,
        database_url=database_url,
        fernet_key=fernet_key,
    )

    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{ig_user_id}/media"
    data = {
        "image_url": post.image_url,
        "caption": post.title,
        "access_token": page_access_token,
    }

    response = requests.post(url, data=data, timeout=60).json()
    if "error" in response:
        raise HTTPException(status_code=400, detail=response["error"])

    post.creation_id = response["id"]
    return {
        "message": "Instagram photo container created",
        "organic_post_index": organic_post_index,
        "creation_id": post.creation_id,
    }


def publish_photo_instagram(
    client_id: str,
    page_id: str,
    organic_post_index: int,
    database_url: str,
    fernet_key: str,
):
    if organic_post_index >= len(organic_posts):
        raise HTTPException(status_code=404, detail="OrganicPost index out of range")

    post = organic_posts[organic_post_index]
    if not post.creation_id:
        raise HTTPException(status_code=400, detail="Creation ID not set for this OrganicPost")

    page_access_token, ig_user_id = _load_page_access_token_and_ig_user_id(
        client_id=client_id,
        page_id=page_id,
        database_url=database_url,
        fernet_key=fernet_key,
    )

    _wait_until_media_finished(post.creation_id, page_access_token)

    publish_url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{ig_user_id}/media_publish"
    data = {
        "creation_id": post.creation_id,
        "access_token": page_access_token,
    }
    publish_resp = requests.post(publish_url, data=data, timeout=60).json()
    if "error" in publish_resp:
        raise HTTPException(status_code=400, detail=publish_resp["error"])

    post.instagram_post_id = publish_resp["id"]
    return {
        "message": "Instagram photo published",
        "organic_post_index": organic_post_index,
        "instagram_post_id": post.instagram_post_id,
    }


# --------------------- Upload CAROUSEL ---------------------
def upload_carousel_instagram(
    client_id: str,
    page_id: str,
    organic_post_index: int,
    database_url: str,
    fernet_key: str,
):
    if organic_post_index >= len(organic_posts):
        raise HTTPException(status_code=404, detail="OrganicPost index out of range")

    post = organic_posts[organic_post_index]
    if not post.carousel_items:
        raise HTTPException(status_code=400, detail="carousel_items not set")

    page_access_token, ig_user_id = _load_page_access_token_and_ig_user_id(
        client_id, page_id, database_url, fernet_key
    )

    child_creation_ids: list[str] = []

    # -------------------------
    # STEP 1 — create children
    # -------------------------
    for idx, item in enumerate(post.carousel_items):
        media_type = item.type.lower()
        media_url = item.url

        endpoint = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{ig_user_id}/media"

        if media_type == "image":
            payload = {
                "image_url": media_url,
                "is_carousel_item": True,
                "access_token": page_access_token,
            }
        elif media_type == "video":
            payload = {
                "media_type": "VIDEO",
                "video_url": media_url,
                "is_carousel_item": True,
                "access_token": page_access_token,
            }
        else:
            raise HTTPException(400, f"Unsupported carousel media type: {media_type}")

        for attempt in range(1, 6):
            resp = requests.post(endpoint, data=payload, timeout=90).json()

            if "error" not in resp:
                child_creation_ids.append(resp["id"])
                break

            err = resp["error"]
            if err.get("is_transient"):
                wait = attempt * 4
                print(f"[carousel child retry {attempt}] transient error, sleeping {wait}s")
                time.sleep(wait)
                continue

            # HARD ERROR
            raise HTTPException(status_code=400, detail=err)

        else:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to create carousel child after retries (index {idx})",
            )

    # --------------------------------------
    # STEP 2 — wait for ALL children to finish
    # --------------------------------------
    for cid in child_creation_ids:
        _wait_until_media_finished(cid, page_access_token)

    # -------------------------------
    # STEP 3 — create parent carousel
    # -------------------------------
    parent_endpoint = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{ig_user_id}/media"
    parent_payload = {
        "media_type": "CAROUSEL",
        "children": ",".join(child_creation_ids),
        "caption": post.title,
        "access_token": page_access_token,
    }

    for attempt in range(1, 6):
        parent_resp = requests.post(parent_endpoint, data=parent_payload, timeout=90).json()

        if "error" not in parent_resp:
            post.creation_id = parent_resp["id"]
            return {
                "message": "Instagram carousel container created",
                "organic_post_index": organic_post_index,
                "creation_id": post.creation_id,
                "children": child_creation_ids,
            }

        err = parent_resp["error"]
        if err.get("is_transient"):
            wait = attempt * 5
            print(f"[carousel parent retry {attempt}] transient error, sleeping {wait}s")
            time.sleep(wait)
            continue

        raise HTTPException(status_code=400, detail=err)

    raise HTTPException(
        status_code=500,
        detail="Failed to create carousel parent after retries",
    )



def publish_carousel_instagram(
    client_id: str,
    page_id: str,
    organic_post_index: int,
    database_url: str,
    fernet_key: str,
):
    if organic_post_index >= len(organic_posts):
        raise HTTPException(status_code=404, detail="OrganicPost index out of range")

    post = organic_posts[organic_post_index]
    if not post.creation_id:
        raise HTTPException(status_code=400, detail="Creation ID not set for this OrganicPost")

    page_access_token, ig_user_id = _load_page_access_token_and_ig_user_id(
        client_id=client_id,
        page_id=page_id,
        database_url=database_url,
        fernet_key=fernet_key,
    )

    _wait_until_media_finished(post.creation_id, page_access_token)

    publish_url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{ig_user_id}/media_publish"
    data = {
        "creation_id": post.creation_id,
        "access_token": page_access_token,
    }
    publish_resp = requests.post(publish_url, data=data, timeout=60).json()
    if "error" in publish_resp:
        raise HTTPException(status_code=400, detail=publish_resp["error"])

    post.instagram_post_id = publish_resp["id"]
    return {
        "message": "Instagram carousel published",
        "organic_post_index": organic_post_index,
        "instagram_post_id": post.instagram_post_id,
    }
