# app/routers/fb_organic_poster.py
from __future__ import annotations

import os
import time
import requests
from typing import Optional

from fastapi import APIRouter, HTTPException

from app.models.schemas import OrganicPost, CarouselItem
from app.routers.DB_helpers.meta_token_db_reader import MetaTokenDbReader

router = APIRouter()
GRAPH_API_VERSION = os.getenv("GRAPH_API_VERSION", "v17.0")

MAX_RETRIES = 20
RETRY_DELAY = 5

# In-memory list (kept for backward compatibility with your pipeline)
organic_posts: list[OrganicPost] = []


# ---------------------------------------------------------------------
# URL NORMALIZATION
# ---------------------------------------------------------------------
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
    """
    For Facebook Page publishing, you only need the Page access token.
    (No IG user id needed.)
    """
    reader = MetaTokenDbReader(database_url=database_url, fernet_key=fernet_key)

    page_token_row = reader.get_active_page_token(client_id=client_id, page_id=page_id)
    if not page_token_row:
        raise HTTPException(status_code=400, detail="No active Page token found in DB.")

    if isinstance(page_token_row, str):
        return page_token_row
    if isinstance(page_token_row, dict):
        return str(page_token_row["access_token"])
    return str(page_token_row.access_token)


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
# FB: VIDEO (uploaded by URL -> published)
# ---------------------------------------------------------------------
@router.post("/fb/organic/upload-video/{organic_post_index}")
def upload_video_facebook(
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

    page_access_token = _load_page_access_token(client_id, page_id, database_url, fernet_key)
    video_url = _normalize_public_media_url(post.video_url)

    # For FB Pages: /{page_id}/videos
    endpoint = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{page_id}/videos"

    # published=false to stage, then publish step can set published=true or create a feed post
    payload = {
        "file_url": video_url,
        "published": "false",
        "description": post.title,  # FB uses description for videos
        "access_token": page_access_token,
    }

    resp = requests.post(endpoint, data=payload, timeout=120).json()
    if "error" in resp:
        raise HTTPException(status_code=400, detail=resp["error"])

    # For /videos, Graph usually returns {"id": "<video_id>"}
    post.creation_id = resp.get("id")
    if not post.creation_id:
        raise HTTPException(status_code=400, detail={"message": "No video id returned", "raw": resp})

    return {"message": "Facebook video uploaded (unpublished)", "creation_id": post.creation_id}


@router.post("/fb/organic/publish-video/{organic_post_index}")
def publish_video_facebook(
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

    page_access_token = _load_page_access_token(client_id, page_id, database_url, fernet_key)

    # Best-effort: some video uploads are async; we can poll status_code on the video object
    _wait_until_media_finished(post.creation_id, page_access_token)

    # Publish by setting published=true on the video object
    endpoint = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{post.creation_id}"
    resp = requests.post(
        endpoint,
        data={"published": "true", "access_token": page_access_token},
        timeout=60,
    ).json()

    if "error" in resp:
        raise HTTPException(status_code=400, detail=resp["error"])

    # Store the "published" video id as post id (FB doesn't always return a feed post id)
    post.facebook_post_id = post.creation_id  # type: ignore[attr-defined]
    return {"message": "Facebook video published", "facebook_post_id": post.facebook_post_id}


# ---------------------------------------------------------------------
# FB: IMAGE (photo)
# ---------------------------------------------------------------------
def upload_photo_facebook(
    client_id: str,
    page_id: str,
    organic_post_index: int,
    database_url: str,
    fernet_key: str,
):
    """
    Facebook Page photo: publish at creation time.
    Graph: POST /{page_id}/photos with published=true
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
            "published": "true",            # IMPORTANT: publish now
            "access_token": page_access_token,
        },
        timeout=90,
    ).json()

    if "error" in resp:
        raise HTTPException(status_code=400, detail=resp["error"])

    # /photos often returns: {"id": "<photo_id>", "post_id": "<pagepost_id>"}
    post.creation_id = resp.get("id")
    post.facebook_post_id = resp.get("post_id") or resp.get("id")  # type: ignore[attr-defined]

    return {
        "message": "Facebook photo published",
        "photo_id": post.creation_id,
        "facebook_post_id": post.facebook_post_id,
    }


def publish_photo_facebook(*args, **kwargs):
    """
    No-op: photo is already published on upload.
    Keep function for pipeline compatibility.
    """
    return {"message": "No-op (photo already published on upload)"}

# ---------------------------------------------------------------------
# FB: CAROUSEL (link post with multiple child attachments)
# NOTE: Facebook "carousel" is typically a link post with child_attachments.
# This requires a link. We'll use the first item as the link by default unless you pass link_url.
# ---------------------------------------------------------------------



import json
import requests
from fastapi import HTTPException
from typing import Optional

# assumes you already have: organic_posts, _load_page_access_token, _normalize_public_media_url, _item_type_url, GRAPH_API_VERSION

def upload_carousel_facebook(
    client_id: str,
    page_id: str,
    organic_post_index: int,
    database_url: str,
    fernet_key: str,
    link_url: Optional[str] = None,
):
    """
    Store the payload only. Publishing happens in publish_carousel_facebook().
    """
    if organic_post_index >= len(organic_posts):
        raise HTTPException(status_code=404, detail="OrganicPost index out of range")

    post = organic_posts[organic_post_index]
    if not post.carousel_items:
        raise HTTPException(status_code=400, detail="carousel_items not set")

    child_attachments: list[dict] = []
    first_image_url: Optional[str] = None

    for item in post.carousel_items:
        media_type, raw_url = _item_type_url(item)
        if media_type != "image":
            raise HTTPException(
                status_code=400,
                detail="FB feed carousel (child_attachments) supports images only. Use option 4 for mixed media.",
            )

        media_url = _normalize_public_media_url(raw_url)
        if not first_image_url:
            first_image_url = media_url

        child_attachments.append(
            {"link": link_url or first_image_url or "", "picture": media_url or ""}
        )

    if len(child_attachments) < 2:
        raise HTTPException(status_code=400, detail="Carousel requires at least 2 images")

    final_link = link_url or first_image_url
    if not final_link:
        raise HTTPException(status_code=400, detail="Missing link_url and no image url available")

    # store payload on the post object (no schema changes needed)
    post._fb_feed_payload = {
        "message": post.title,
        "link": final_link,
        "child_attachments": json.dumps(child_attachments),
    }

    return {"message": "FB carousel payload stored", "ready": True}


def publish_carousel_facebook(
    client_id: str,
    page_id: str,
    organic_post_index: int,
    database_url: str,
    fernet_key: str,
):
    if organic_post_index >= len(organic_posts):
        raise HTTPException(status_code=404, detail="OrganicPost index out of range")

    post = organic_posts[organic_post_index]
    payload = getattr(post, "_fb_feed_payload", None)
    if not payload:
        raise HTTPException(status_code=400, detail="No stored payload. Call upload first.")

    page_access_token = _load_page_access_token(client_id, page_id, database_url, fernet_key)

    endpoint = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{page_id}/feed"
    resp = requests.post(
        endpoint,
        data={
            **payload,
            "is_published": "true",
            "access_token": page_access_token,
        },
        timeout=90,
    ).json()

    if "error" in resp:
        raise HTTPException(status_code=400, detail=resp["error"])

    # If your OrganicPost schema does not have facebook_post_id, do NOT assign it.
    return {"message": "FB carousel published", "facebook_post_id": resp.get("id")}
def publish_mixed_media_bundle_facebook(
    client_id: str,
    page_id: str,
    organic_post_index: int,
    database_url: str,
    fernet_key: str,
):
    """
    Mixed media bundle publishing for Facebook Pages:
    - videos -> 1+ video posts
    - images -> 1 album-style multi-photo post (attached_media)

    This is NOT a single mixed carousel post (FB doesn't support that like IG).
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
            if not media_url:
                continue
            image_urls.append(media_url)
        elif media_type == "video":
            if not media_url:
                continue
            video_urls.append(media_url)
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported media type: {media_type}")

    if not image_urls and not video_urls:
        raise HTTPException(status_code=400, detail="No valid image/video URLs found")

    results: dict = {"videos": [], "photo_post": None}

    # -------------------------
    # A) Publish videos (each becomes its own post)
    # -------------------------
    for i, vurl in enumerate(video_urls, start=1):
        endpoint = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{page_id}/videos"

        # If multiple videos, annotate caption
        description = post.title
        if len(video_urls) > 1:
            description = f"{post.title} (Video {i}/{len(video_urls)})"

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

    # -------------------------
    # B) Publish images as ONE multi-photo feed post (album-style)
    # -------------------------
    if image_urls:
        photo_ids: list[str] = []

        # Step 1: upload each photo unpublished to get media_fbid
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

        # Step 2: create feed post referencing uploaded photos
        attached_media = [{"media_fbid": pid} for pid in photo_ids]

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