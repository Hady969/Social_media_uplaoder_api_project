# app/routers/organic_poster.py
from __future__ import annotations

import os
import time
import requests
from typing import List, Optional

from fastapi import APIRouter, HTTPException

from app.models.schemas import OrganicPost, CarouselItem
from app.routers.DB_helpers.meta_token_db_reader import MetaTokenDbReader

router = APIRouter()
GRAPH_API_VERSION = "v17.0"

# In-memory list (kept for backward compatibility with your pipeline)
organic_posts: List[OrganicPost] = []

MAX_RETRIES = 20
RETRY_DELAY = 5


# ---------------------------------------------------------------------
# URL NORMALIZATION
# ---------------------------------------------------------------------
# With DigitalOcean Spaces, URLs are already public.
# This helper is kept for safety / backward compatibility.
PUBLIC_BASE_URL = (os.getenv("PUBLIC_BASE_URL") or os.getenv("DROPLET_PUBLIC_BASE_URL") or "").strip().rstrip("/")
NGROK_BASE_URL = (os.getenv("NGROK_BASE_URL") or "").strip().rstrip("/")


def _normalize_public_media_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return url

    u = str(url).strip()
    if not u:
        return u

    # Already a public URL (Spaces/CDN/etc.)
    if u.startswith("http://") or u.startswith("https://"):
        if NGROK_BASE_URL and PUBLIC_BASE_URL and u.startswith(NGROK_BASE_URL):
            return PUBLIC_BASE_URL + u[len(NGROK_BASE_URL):]
        return u

    # Relative/local fallback (should not happen with Spaces)
    if not PUBLIC_BASE_URL:
        raise HTTPException(
            status_code=500,
            detail="PUBLIC_BASE_URL is not set and a non-public media URL was provided.",
        )

    if u.startswith("/"):
        return f"{PUBLIC_BASE_URL}{u}"
    return f"{PUBLIC_BASE_URL}/{u}"


# ---------------------------------------------------------------------
# META HELPERS
# ---------------------------------------------------------------------
def _load_page_access_token_and_ig_user_id(
    client_id: str,
    page_id: str,
    database_url: str,
    fernet_key: str,
) -> tuple[str, str]:
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
                "No Instagram account linked to this client. "
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
    if hasattr(item, "type"):
        return (item.type or "").strip().lower(), getattr(item, "url", None)
    if isinstance(item, dict):
        return (item.get("type") or "").strip().lower(), item.get("url")
    return "", None


# ---------------------------------------------------------------------
# VIDEO (REEL)
# ---------------------------------------------------------------------
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
    if not post.video_url:
        raise HTTPException(status_code=400, detail="video_url not set")

    page_access_token, ig_user_id = _load_page_access_token_and_ig_user_id(
        client_id, page_id, database_url, fernet_key
    )

    video_url = _normalize_public_media_url(post.video_url)

    endpoint = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{ig_user_id}/media"
    payload = {
        "media_type": "REELS",
        "video_url": video_url,
        "caption": post.title,
        "access_token": page_access_token,
    }

    resp = requests.post(endpoint, data=payload, timeout=60).json()
    if "error" in resp:
        raise HTTPException(status_code=400, detail=resp["error"])

    post.creation_id = resp["id"]
    return {"message": "Instagram video container created", "creation_id": post.creation_id}


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
        raise HTTPException(status_code=400, detail="Creation ID not set")

    page_access_token, ig_user_id = _load_page_access_token_and_ig_user_id(
        client_id, page_id, database_url, fernet_key
    )

    _wait_until_media_finished(post.creation_id, page_access_token)

    publish_url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{ig_user_id}/media_publish"
    resp = requests.post(
        publish_url,
        data={"creation_id": post.creation_id, "access_token": page_access_token},
        timeout=60,
    ).json()

    if "error" in resp:
        raise HTTPException(status_code=400, detail=resp["error"])

    post.instagram_post_id = resp["id"]
    return {"message": "Instagram video published", "instagram_post_id": post.instagram_post_id}


# ---------------------------------------------------------------------
# IMAGE (SINGLE)
# ---------------------------------------------------------------------
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
        raise HTTPException(status_code=400, detail="image_url not set")

    page_access_token, ig_user_id = _load_page_access_token_and_ig_user_id(
        client_id, page_id, database_url, fernet_key
    )

    image_url = _normalize_public_media_url(post.image_url)

    endpoint = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{ig_user_id}/media"
    resp = requests.post(
        endpoint,
        data={
            "image_url": image_url,
            "caption": post.title,
            "access_token": page_access_token,
        },
        timeout=60,
    ).json()

    if "error" in resp:
        raise HTTPException(status_code=400, detail=resp["error"])

    post.creation_id = resp["id"]
    return {"message": "Instagram photo container created", "creation_id": post.creation_id}


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
        raise HTTPException(status_code=400, detail="Creation ID not set")

    page_access_token, ig_user_id = _load_page_access_token_and_ig_user_id(
        client_id, page_id, database_url, fernet_key
    )

    _wait_until_media_finished(post.creation_id, page_access_token)

    publish_url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{ig_user_id}/media_publish"
    resp = requests.post(
        publish_url,
        data={"creation_id": post.creation_id, "access_token": page_access_token},
        timeout=60,
    ).json()

    if "error" in resp:
        raise HTTPException(status_code=400, detail=resp["error"])

    post.instagram_post_id = resp["id"]
    return {"message": "Instagram photo published", "instagram_post_id": post.instagram_post_id}


# ---------------------------------------------------------------------
# CAROUSEL
# ---------------------------------------------------------------------
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

    child_ids: list[str] = []

    # STEP 1 — children
    for idx, item in enumerate(post.carousel_items):
        media_type, raw_url = _item_type_url(item)
        media_url = _normalize_public_media_url(raw_url)

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

        resp = requests.post(endpoint, data=payload, timeout=90).json()
        if "error" in resp:
            raise HTTPException(status_code=400, detail=resp["error"])

        child_ids.append(resp["id"])

    # STEP 2 — wait
    for cid in child_ids:
        _wait_until_media_finished(cid, page_access_token)

    # STEP 3 — parent
    parent_endpoint = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{ig_user_id}/media"
    parent_resp = requests.post(
        parent_endpoint,
        data={
            "media_type": "CAROUSEL",
            "children": ",".join(child_ids),
            "caption": post.title,
            "access_token": page_access_token,
        },
        timeout=90,
    ).json()

    if "error" in parent_resp:
        raise HTTPException(status_code=400, detail=parent_resp["error"])

    post.creation_id = parent_resp["id"]
    return {"message": "Instagram carousel container created", "creation_id": post.creation_id}


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
        raise HTTPException(status_code=400, detail="Creation ID not set")

    page_access_token, ig_user_id = _load_page_access_token_and_ig_user_id(
        client_id, page_id, database_url, fernet_key
    )

    _wait_until_media_finished(post.creation_id, page_access_token)

    publish_url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{ig_user_id}/media_publish"
    resp = requests.post(
        publish_url,
        data={"creation_id": post.creation_id, "access_token": page_access_token},
        timeout=60,
    ).json()

    if "error" in resp:
        raise HTTPException(status_code=400, detail=resp["error"])

    post.instagram_post_id = resp["id"]
    return {"message": "Instagram carousel published", "instagram_post_id": post.instagram_post_id}
