# app/console/main_fb_organic_pipeline.py
from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()

import os
from pathlib import Path

from fastapi import UploadFile

from app.models.spaces_uploader import SpacesUploader
from app.routers.DB_helpers.meta_token_db_reader import MetaTokenDbReader
from app.models.schemas import OrganicPost, CarouselItem
from app.models.fb_organic_poster import publish_mixed_media_bundle_facebook


# IMPORTANT: FB-only imports (no IG endpoints)
from app.models.fb_organic_poster import (
    organic_posts,
    upload_video_facebook,
    publish_video_facebook,
    upload_photo_facebook,
    publish_photo_facebook,
    upload_carousel_facebook,
    publish_carousel_facebook,
)

# ---------------- CONFIG ----------------
DEFAULT_VIDEO_PATH = r"C:\Users\User\Desktop\Ig_Reels\istockphoto-2097298327-640_adpp_is.mp4"
DEFAULT_IMAGE_PATH = r"C:\Users\User\Pictures\example.jpg"
DEFAULT_TITLE = "AI is changing everything 🤖"
DEFAULT_LINK_URL = "https://example.com"  # required for FB carousel feed posts

CLIENT_ID = os.environ["CLIENT_ID"]
DATABASE_URL = os.environ["DATABASE_URL"]
FERNET_KEY = os.environ["TOKEN_ENCRYPTION_KEY"]

uploader = SpacesUploader()


# ---------------- PROMPTS ----------------
def prompt_str(label: str, default: str) -> str:
    print(f"\n{label} (press Enter to keep default)")
    print(f"Default: {default}")
    raw = input("> ").strip()
    return raw if raw else default


def prompt_path(label: str, default: str) -> str:
    print(f"\n{label} path (press Enter to keep default)")
    print(f"Default: {default}")
    raw = input("> ").strip()
    return raw if raw else default


def prompt_asset_type() -> str:
    print("\nChoose Facebook post type:")
    print("1) Video")
    print("2) Image (Single photo)")
    print("3) Carousel (images only)")
    print("4) Mixed bundle (images + videos)")
    while True:
        choice = input("> ").strip()
        if choice == "1":
            return "video"
        if choice == "2":
            return "image"
        if choice == "3":
            return "carousel"
        if choice == "4":
            return "mixed"
        print("Invalid choice. Please select 1, 2, 3, or 4.")



def _ext_is_video(ext: str) -> bool:
    return ext in {".mp4", ".mov", ".m4v"}


def _ext_is_image(ext: str) -> bool:
    return ext in {".jpg", ".jpeg", ".png", ".webp"}


# ---------------- MAIN ----------------
def main() -> None:
    reader = MetaTokenDbReader(database_url=DATABASE_URL, fernet_key=FERNET_KEY)

    # ---------- Resolve page_id from DB ----------
    meta_page = reader.get_latest_meta_page_for_client(CLIENT_ID)
    page_id = (meta_page or {}).get("page_id")
    if not page_id:
        raise RuntimeError("No page_id found for this client in DB.")

    print("Resolved Facebook page_id:", page_id)
    # Optional sanity: compare with IG actor id (must be different)
    try:
        ig_actor = reader.get_instagram_actor_id_for_client(CLIENT_ID)
        print("Resolved Instagram actor id (for sanity):", ig_actor)
    except Exception:
        pass

    # ---------- User input ----------
    title = prompt_str("Caption / Title", DEFAULT_TITLE)
    asset_type = prompt_asset_type()

    organic_post: OrganicPost

    # ================= VIDEO =================
    if asset_type == "video":
        video_path = prompt_path("Video", DEFAULT_VIDEO_PATH)
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video not found: {video_path}")

        with open(video_path, "rb") as f:
            upload_file = UploadFile(filename=os.path.basename(video_path), file=f)

            # Upload to Spaces to get a PUBLIC URL (required for Graph file_url)
            video_url = uploader.upload_organic_video(
                fileobj=upload_file.file,
                filename=upload_file.filename,
                content_type=upload_file.content_type or "video/mp4",
            )

        organic_post = OrganicPost(title=title, video_url=video_url)
        print(f"[spaces] Video URL: {organic_post.video_url}")

        organic_posts.append(organic_post)
        idx = len(organic_posts) - 1

        # HARD sanity: show exactly what is being called
        print("CALLING:", upload_video_facebook.__module__, upload_video_facebook.__name__)
        print("CALLING:", publish_video_facebook.__module__, publish_video_facebook.__name__)

        # FB-only: /{page_id}/videos (NOT IG /media)
        upload_video_facebook(CLIENT_ID, str(page_id), idx, DATABASE_URL, FERNET_KEY)
        publish_video_facebook(CLIENT_ID, str(page_id), idx, DATABASE_URL, FERNET_KEY)

    # ================= IMAGE =================
    elif asset_type == "image":
        image_path = prompt_path("Image", DEFAULT_IMAGE_PATH)
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"Image not found: {image_path}")

        with open(image_path, "rb") as f:
            upload_file = UploadFile(filename=os.path.basename(image_path), file=f)

            image_url = uploader.upload_organic_image(
                fileobj=upload_file.file,
                filename=upload_file.filename,
                content_type=upload_file.content_type or "image/jpeg",
            )

        organic_post = OrganicPost(title=title, image_url=image_url)
        print(f"[spaces] Image URL: {organic_post.image_url}")

        organic_posts.append(organic_post)
        idx = len(organic_posts) - 1

        print("CALLING:", upload_photo_facebook.__module__, upload_photo_facebook.__name__)
        print("CALLING:", publish_photo_facebook.__module__, publish_photo_facebook.__name__)

        upload_photo_facebook(CLIENT_ID, str(page_id), idx, DATABASE_URL, FERNET_KEY)
        publish_photo_facebook(CLIENT_ID, str(page_id), idx, DATABASE_URL, FERNET_KEY)

    # ================= CAROUSEL (IMAGES ONLY) =================
    elif asset_type == "carousel":
        print("\nCarousel setup:")
        print("Enter paths to images ONLY. Leave empty to finish.")

        items: list[CarouselItem] = []

        while True:
            path = input("Media path: ").strip()
            if not path:
                break

            if not os.path.exists(path):
                print("File not found, try again.")
                continue

            ext = Path(path).suffix.lower()
            if not _ext_is_image(ext):
                print("Unsupported file type for FB carousel. Use images only (.jpg/.png/.webp).")
                continue

            with open(path, "rb") as f:
                upload_file = UploadFile(filename=os.path.basename(path), file=f)

                url = uploader.upload_organic_image(
                    fileobj=upload_file.file,
                    filename=upload_file.filename,
                    content_type=upload_file.content_type or "image/jpeg",
                )
                items.append(CarouselItem(type="image", url=url))
                print(f"[spaces] Carousel image: {url}")

        if len(items) < 2:
            raise RuntimeError("Carousel requires at least 2 images.")

        link_url = prompt_str("Link URL for Facebook carousel post", DEFAULT_LINK_URL)

        organic_post = OrganicPost(title=title, carousel_items=items)
        organic_posts.append(organic_post)
        idx = len(organic_posts) - 1

        print("CALLING:", upload_carousel_facebook.__module__, upload_carousel_facebook.__name__)
        print("CALLING:", publish_carousel_facebook.__module__, publish_carousel_facebook.__name__)

        # publish step posts to /{page_id}/feed with is_published=true
        upload_carousel_facebook(CLIENT_ID, str(page_id), idx, DATABASE_URL, FERNET_KEY, link_url=link_url)
        publish_carousel_facebook(CLIENT_ID, str(page_id), idx, DATABASE_URL, FERNET_KEY)

    # ================= MIXED (IMAGES + VIDEOS) =================
    elif asset_type == "mixed":
        print("\nMixed setup:")
        print("Enter paths to images/videos. Leave empty to finish.")

        items: list[CarouselItem] = []

        while True:
            path = input("Media path: ").strip()
            if not path:
                break

            if not os.path.exists(path):
                print("File not found, try again.")
                continue

            ext = Path(path).suffix.lower()
            if not (_ext_is_image(ext) or _ext_is_video(ext)):
                print("Unsupported file type. Use .jpg/.png/.webp or .mp4/.mov/.m4v")
                continue

            with open(path, "rb") as f:
                upload_file = UploadFile(filename=os.path.basename(path), file=f)

                if _ext_is_video(ext):
                    url = uploader.upload_organic_video(
                        fileobj=upload_file.file,
                        filename=upload_file.filename,
                        content_type=upload_file.content_type or "video/mp4",
                    )
                    items.append(CarouselItem(type="video", url=url))
                    print(f"[spaces] Mixed video: {url}")
                else:
                    url = uploader.upload_organic_image(
                        fileobj=upload_file.file,
                        filename=upload_file.filename,
                        content_type=upload_file.content_type or "image/jpeg",
                    )
                    items.append(CarouselItem(type="image", url=url))
                    print(f"[spaces] Mixed image: {url}")

        if len(items) < 2:
            raise RuntimeError("Mixed bundle requires at least 2 items total.")

        organic_post = OrganicPost(title=title, carousel_items=items)
        organic_posts.append(organic_post)
        idx = len(organic_posts) - 1

        print(
            "CALLING:",
            publish_mixed_media_bundle_facebook.__module__,
            publish_mixed_media_bundle_facebook.__name__,
        )
        result = publish_mixed_media_bundle_facebook(CLIENT_ID, str(page_id), idx, DATABASE_URL, FERNET_KEY)
        print(result)

    else:
        raise RuntimeError(f"Unknown asset_type: {asset_type}")

    # ---------- Final output ----------
    print("\n✅ Facebook organic post flow finished")
    try:
        print(organic_post.model_dump())
    except Exception:
        print(organic_post.dict())



if __name__ == "__main__":
    main()
