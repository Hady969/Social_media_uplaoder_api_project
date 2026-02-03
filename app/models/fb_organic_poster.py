# app/models/fb_organic_poster.py
from __future__ import annotations

import json
import os
import time
from typing import Optional, Tuple, Any

import requests
from fastapi import APIRouter, HTTPException

from app.models.schemas import OrganicPost, CarouselItem
from app.routers.DB_helpers.meta_token_db_reader import MetaTokenDbReader

router = APIRouter()
GRAPH_API_VERSION = os.getenv("GRAPH_API_VERSION", "v19.0")

# In-memory list (pipeline compatibility)
organic_posts: list[OrganicPost] = []

MAX_RETRIES = 20
RETRY_DELAY = 5

PUBLIC_BASE_URL = (os.getenv("PUBLIC_BASE_URL") or os.getenv("DROPLET_PUBLIC_BASE_URL") or "").strip().rstrip("/")
NGROK_BASE_URL = (os.getenv("NGROK_BASE_URL") or "").strip().rstrip("/")


# ---------------------------------------------------------------------
# URL NORMALIZATION
# ---------------------------------------------------------------------
def _normalize_public_media_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return url

    u = str(url).strip()
    if not u:
        return u

    # Already a public URL (Spaces/CDN/etc.)
    if u.startswith("http://") or u.startswith("https://"):
        if NGROK_BASE_URL and PUBLIC_BASE_URL and u.startswith(NGROK_BASE_URL):
            return PUBLIC_BASE_URL + u[len(NGROK_BASE_URL) :]
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
def _load_page_access_token(
    client_id: str,
    page_id: str,
    database_url: str,
    fernet_key: str,
) -> str:
    reader = MetaTokenDbReader(database_url=database_url, fernet_key=fernet_key)

    page_token_row = reader.get_active_page_token(client_id=client_id, page_id=page_id)
    if isinstance(page_token_row, str):
        return page_token_row
    if isinstance(page_token_row, dict):
        return str(page_token_row["access_token"])
    return str(page_token_row.access_token)


# ---------------------------------------------------------------------
# IMPORTANT NOTE ABOUT "WAIT"
# ---------------------------------------------------------------------
# Instagram media containers expose status_code (FINISHED/ERROR).
# Facebook Video nodes DO NOT expose status_code.
# Therefore: DO NOT call _wait_until_media_finished() for FB videos.
#
# For FB carousel (image-only link carousel using child_attachments),
# we also do NOT need a status poll.
#
# For multi-photo attached_media workflow, we do not poll; we just post.
# ---------------------------------------------------------------------


def _item_type_url(item: Any) -> tuple[str, Optional[str]]:
    if hasattr(item, "type"):
        return (str(getattr(item, "type") or "").strip().lower(), getattr(item, "url", None))
    if isinstance(item, dict):
        return (str(item.get("type") or "").strip().lower(), item.get("url"))
    return "", None


# ---------------------------------------------------------------------
# FB VIDEO (single step publish)
# ---------------------------------------------------------------------
@router.post("/fb/organic/upload-video/{organic_post_index}")
def upload_video_facebook(
    client_id: str,
    page_id: str,
    organic_post_index: int,
    database_url: str,
    fernet_key: str,
):
    """
    Facebook Page video publish happens at upload time:
    POST /{page_id}/videos with file_url and published=true
    """
    if organic_post_index >= len(organic_posts):
        raise HTTPException(status_code=404, detail="OrganicPost index out of range")

    post = organic_posts[organic_post_index]
    if not post.video_url:
        raise HTTPException(status_code=400, detail="video_url not set")

    page_access_token = _load_page_access_token(client_id, page_id, database_url, fernet_key)
    video_url = _normalize_public_media_url(post.video_url)

    endpoint = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{page_id}/videos"
    resp = requests.post(
        endpoint,
        data={
            "file_url": video_url,
            "published": "true",
            "description": post.title,
            "access_token": page_access_token,
        },
        timeout=180,
    ).json()

    if "error" in resp:
        raise HTTPException(status_code=400, detail=resp["error"])

    # Store only fields that exist on OrganicPost
    post.creation_id = resp.get("id")
    return {"message": "Facebook video published", "video_id": post.creation_id}


@router.post("/fb/organic/publish-video/{organic_post_index}")
def publish_video_facebook(
    client_id: str,
    page_id: str,
    organic_post_index: int,
    database_url: str,
    fernet_key: str,
):
    """
    NO-OP by design: FB videos are published during upload.
    Kept only for backward compatibility with your pipeline.
    """
    return {"message": "No-op (FB video already published on upload)"}


# ---------------------------------------------------------------------
# FB IMAGE (single step publish)
# ---------------------------------------------------------------------
@router.post("/fb/organic/upload-photo/{organic_post_index}")
def upload_photo_facebook(
    client_id: str,
    page_id: str,
    organic_post_index: int,
    database_url: str,
    fernet_key: str,
):
    """
    Facebook Page photo publish happens at upload time:
    POST /{page_id}/photos with url and published=true
    """
    if organic_post_index >= len(organic_posts):
        raise HTTPException(status_code=404, detail="OrganicPost index out of range")

    post = organic_posts[organic_post_index]
    if not post.image_url:
        raise HTTPException(status_code=400, detail="image_url not set")

    page_access_token = _load_page_access_token(client_id, page_id, database_url, fernet_key)
    image_url = _normalize_public_media_url(post.image_url)

    endpoint = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{page_id}/photos"
    resp = requests.post(
        endpoint,
        data={
            "url": image_url,
            "caption": post.title,
            "published": "true",
            "access_token": page_access_token,
        },
        timeout=90,
    ).json()

    if "error" in resp:
        raise HTTPException(status_code=400, detail=resp["error"])

    post.creation_id = resp.get("id")
    return {"message": "Facebook photo published", "photo_id": post.creation_id, "post_id": resp.get("post_id")}


@router.post("/fb/organic/publish-photo/{organic_post_index}")
def publish_photo_facebook(
    client_id: str,
    page_id: str,
    organic_post_index: int,
    database_url: str,
    fernet_key: str,
):
    """
    NO-OP by design: FB photos are published during upload.
    Kept only for backward compatibility with your pipeline.
    """
    return {"message": "No-op (FB photo already published on upload)"}


# ---------------------------------------------------------------------
# FB CAROUSEL (images only, link carousel via child_attachments)
# ---------------------------------------------------------------------
@router.post("/fb/organic/upload-carousel/{organic_post_index}")
def upload_carousel_facebook(
    client_id: str,
    page_id: str,
    organic_post_index: int,
    database_url: str,
    fernet_key: str,
    link_url: Optional[str] = None,
):
    """
    Store a payload for a link carousel-like post (child_attachments).
    This supports IMAGES ONLY for FB organic feed.
    """
    if organic_post_index >= len(organic_posts):
        raise HTTPException(status_code=404, detail="OrganicPost index out of range")

    post = organic_posts[organic_post_index]
    if not post.carousel_items:
        raise HTTPException(status_code=400, detail="carousel_items not set")

    child_attachments: list[dict] = []
    first_image: Optional[str] = None

    for item in post.carousel_items:
        media_type, raw_url = _item_type_url(item)
        if media_type != "image":
            raise HTTPException(
                status_code=400,
                detail="FB carousel (child_attachments) supports images only. Use mixed option for videos.",
            )

        media_url = _normalize_public_media_url(raw_url)
        if not media_url:
            continue

        if not first_image:
            first_image = media_url

        child_attachments.append(
            {
                "link": link_url or first_image or "",
                "picture": media_url,
            }
        )

    if len(child_attachments) < 2:
        raise HTTPException(status_code=400, detail="Carousel requires at least 2 images")

    final_link = link_url or first_image
    if not final_link:
        raise HTTPException(status_code=400, detail="Missing link_url and no image URL available")

    # Store on the post object (no Pydantic schema changes)
    post._fb_feed_payload = {
        "message": post.title,
        "link": final_link,
        "child_attachments": json.dumps(child_attachments),
    }
    return {"message": "FB carousel payload stored", "ready": True}


@router.post("/fb/organic/publish-carousel/{organic_post_index}")
def publish_carousel_facebook(
    client_id: str,
    page_id: str,
    organic_post_index: int,
    database_url: str,
    fernet_key: str,
):
    """
    Publish the link carousel-like post:
    POST /{page_id}/feed with message/link/child_attachments
    """
    if organic_post_index >= len(organic_posts):
        raise HTTPException(status_code=404, detail="OrganicPost index out of range")

    post = organic_posts[organic_post_index]
    payload = getattr(post, "_fb_feed_payload", None)
    if not payload:
        raise HTTPException(status_code=400, detail="No stored payload. Call upload_carousel_facebook first.")

    page_access_token = _load_page_access_token(client_id, page_id, database_url, fernet_key)

    endpoint = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{page_id}/feed"
    resp = requests.post(
        endpoint,
        data={**payload, "is_published": "true", "access_token": page_access_token},
        timeout=90,
    ).json()

    if "error" in resp:
        raise HTTPException(status_code=400, detail=resp["error"])

    return {"message": "FB carousel published", "facebook_post_id": resp.get("id")}


# ---------------------------------------------------------------------
# FB MIXED BUNDLE (images + videos)
# - videos => 1+ video posts
# - images => 1 multi-photo post using attached_media
# ---------------------------------------------------------------------
@router.post("/fb/organic/publish-mixed/{organic_post_index}")
def publish_mixed_media_bundle_facebook(
    client_id: str,
    page_id: str,
    organic_post_index: int,
    database_url: str,
    fernet_key: str,
):
    """
    Facebook does NOT support a true mixed (image+video) carousel in a single organic feed swipe post.
    This publishes:
      - each video as its own /{page_id}/videos post
      - all images as one multi-photo /{page_id}/feed post (attached_media)
    """
    if organic_post_index >= len(organic_posts):
        raise HTTPException(status_code=404, detail="OrganicPost index out of range")

    post = organic_posts[organic_post_index]
    if not post.carousel_items:
        raise HTTPException(status_code=400, detail="carousel_items not set")

    page_access_token = _load_page_access_token(client_id, page_id, database_url, fernet_key)

    image_urls: list[str] = []
    video_urls: list[str] = []

    for item in post.carousel_items:
        media_type, raw_url = _item_type_url(item)
        media_url = _normalize_public_media_url(raw_url)

        if media_type == "image":
            if media_url:
                image_urls.append(media_url)
        elif media_type == "video":
            if media_url:
                video_urls.append(media_url)
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported media type: {media_type}")

    if not image_urls and not video_urls:
        raise HTTPException(status_code=400, detail="No valid image/video URLs found")

    results: dict = {"videos": [], "photo_post": None}

    # A) publish videos (each becomes its own post)
    for i, vurl in enumerate(video_urls, start=1):
        endpoint = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{page_id}/videos"
        description = post.title if len(video_urls) == 1 else f"{post.title} (Video {i}/{len(video_urls)})"

        resp = requests.post(
            endpoint,
            data={
                "file_url": vurl,
                "published": "true",
                "description": description,
                "access_token": page_access_token,
            },
            timeout=180,
        ).json()

        if "error" in resp:
            raise HTTPException(status_code=400, detail={"stage": "video_publish", "error": resp["error"]})

        results["videos"].append({"video_id": resp.get("id")})

    # B) publish images as ONE multi-photo post (attached_media)
    if image_urls:
        photo_ids: list[str] = []

        # Step 1: upload each photo unpublished
        for img_url in image_urls:
            up = requests.post(
                f"https://graph.facebook.com/{GRAPH_API_VERSION}/{page_id}/photos",
                data={
                    "url": img_url,
                    "published": "false",
                    "access_token": page_access_token,
                },
                timeout=90,
            ).json()

            if "error" in up:
                raise HTTPException(status_code=400, detail={"stage": "photo_upload", "error": up["error"]})

            pid = up.get("id")
            if not pid:
                raise HTTPException(status_code=400, detail={"stage": "photo_upload", "raw": up})
            photo_ids.append(pid)

        attached_media = [{"media_fbid": pid} for pid in photo_ids]

        # Step 2: create feed post referencing those photos
        feed = requests.post(
            f"https://graph.facebook.com/{GRAPH_API_VERSION}/{page_id}/feed",
            data={
                "message": post.title,
                "attached_media": json.dumps(attached_media),
                "access_token": page_access_token,
            },
            timeout=90,
        ).json()

        if "error" in feed:
            raise HTTPException(status_code=400, detail={"stage": "photo_post_publish", "error": feed["error"]})

        results["photo_post"] = {"facebook_post_id": feed.get("id"), "photo_ids": photo_ids}

    return {"message": "Mixed media bundle published", "result": results}
