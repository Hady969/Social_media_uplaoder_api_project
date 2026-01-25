# app/routers/main_organic_pipeline.py
from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()

import os
from pathlib import Path
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.routers.ngrok_media_manager import (
    save_organic_video,
    save_organic_image,
)
from app.routers.organic_poster import (
    organic_posts,
    upload_video_instagram,
    publish_video_instagram,
    upload_photo_instagram,
    publish_photo_instagram,
    upload_carousel_instagram,
    publish_carousel_instagram,
)
from app.routers.meta_token_db_reader import MetaTokenDbReader
from app.models.schemas import OrganicPost


# ---------------- CONFIG ----------------
DEFAULT_VIDEO_PATH = r"C:\Users\User\Desktop\Ig_Reels\istockphoto-2097298327-640_adpp_is.mp4"
DEFAULT_IMAGE_PATH = r"C:\Users\User\Pictures\example.jpg"
DEFAULT_TITLE = "AI is changing everything 🤖"

CLIENT_ID = os.environ["CLIENT_ID"]
DATABASE_URL = os.environ["DATABASE_URL"]
FERNET_KEY = os.environ["TOKEN_ENCRYPTION_KEY"]


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
    print("\nChoose organic post type:")
    print("1) Video (Reel)")
    print("2) Image (Single photo)")
    print("3) Carousel (multiple images / videos)")
    while True:
        choice = input("> ").strip()
        if choice == "1":
            return "video"
        if choice == "2":
            return "image"
        if choice == "3":
            return "carousel"
        print("Invalid choice. Please select 1, 2, or 3.")


# ---------------- MAIN ----------------
def main() -> None:
    reader = MetaTokenDbReader(database_url=DATABASE_URL, fernet_key=FERNET_KEY)

    # ---------- Resolve page_id from DB ----------
    meta_page = reader.get_latest_meta_page_for_client(CLIENT_ID)
    page_id = meta_page.get("page_id")
    if not page_id:
        raise RuntimeError("No page_id found for this client in DB.")

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
            upload_file = StarletteUploadFile(
                filename=os.path.basename(video_path),
                file=f,
            )
            organic_post = save_organic_video(title, upload_file)

        print(f"[ngrok] Video URL: {organic_post.video_url}")

        organic_posts.append(organic_post)
        idx = len(organic_posts) - 1

        upload_video_instagram(CLIENT_ID, str(page_id), idx, DATABASE_URL, FERNET_KEY)
        publish_video_instagram(CLIENT_ID, str(page_id), idx, DATABASE_URL, FERNET_KEY)

    # ================= IMAGE =================
    elif asset_type == "image":
        image_path = prompt_path("Image", DEFAULT_IMAGE_PATH)
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"Image not found: {image_path}")

        with open(image_path, "rb") as f:
            upload_file = StarletteUploadFile(
                filename=os.path.basename(image_path),
                file=f,
            )
            organic_post = save_organic_image(title, upload_file)

        print(f"[ngrok] Image URL: {organic_post.image_url}")

        organic_posts.append(organic_post)
        idx = len(organic_posts) - 1

        upload_photo_instagram(CLIENT_ID, str(page_id), idx, DATABASE_URL, FERNET_KEY)
        publish_photo_instagram(CLIENT_ID, str(page_id), idx, DATABASE_URL, FERNET_KEY)

    # ================= CAROUSEL =================
    else:
        print("\nCarousel setup:")
        print("Enter paths to images/videos. Leave empty to finish.")

        items = []
        while True:
            path = input("Media path: ").strip()
            if not path:
                break

            if not os.path.exists(path):
                print("File not found, try again.")
                continue

            ext = Path(path).suffix.lower()
            media_type = "video" if ext in {".mp4", ".mov"} else "image"

            with open(path, "rb") as f:
                upload_file = StarletteUploadFile(
                    filename=os.path.basename(path),
                    file=f,
                )
                if media_type == "video":
                    post_part = save_organic_video("", upload_file)
                    items.append({"type": "video", "url": post_part.video_url})
                else:
                    post_part = save_organic_image("", upload_file)
                    items.append({"type": "image", "url": post_part.image_url})

        if len(items) < 2:
            raise RuntimeError("Carousel requires at least 2 items.")

        organic_post = OrganicPost(
            title=title,
            carousel_items=items,
        )

        organic_posts.append(organic_post)
        idx = len(organic_posts) - 1

        upload_carousel_instagram(CLIENT_ID, str(page_id), idx, DATABASE_URL, FERNET_KEY)
        publish_carousel_instagram(CLIENT_ID, str(page_id), idx, DATABASE_URL, FERNET_KEY)

    # ---------- Final output ----------
    print("\n✅ Organic post published successfully")
    try:
        print(organic_post.model_dump())
    except Exception:
        print(organic_post.dict())


if __name__ == "__main__":
    main()
