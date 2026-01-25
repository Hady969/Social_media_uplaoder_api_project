# app/routers/main_ad_pipeline.py
from __future__ import annotations

import os
import json
import traceback
import mimetypes
from pathlib import Path

from fastapi import UploadFile
from dotenv import load_dotenv

from app.models.ads_stairway import AdsStairway
from app.routers.meta_token_db_reader import MetaTokenDbReader
from app.routers.ngrok_media_manager import save_ad_media

load_dotenv()

# ---------------- CONFIG ----------------
VIDEO_PATH = r"C:\Users\User\Desktop\Ig_Reels\istockphoto-2097298327-640_adpp_is.mp4"
IMAGE_PATH = r"C:\Users\User\Pictures\example.jpg"

CLIENT_ID = os.environ["CLIENT_ID"]  # tenant selector
GRAPH_API_VERSION = os.getenv("GRAPH_API_VERSION", "v17.0")

DATABASE_URL = os.environ["DATABASE_URL"]
FERNET_KEY = os.environ["TOKEN_ENCRYPTION_KEY"]

CAMPAIGN_NAME = "Automated IG Campaign_DB"
OBJECTIVE = "OUTCOME_AWARENESS"
INDEX = 0

DEFAULT_LINK_URL = os.getenv("DEFAULT_AD_LINK_URL", "https://www.instagram.com/")

# Status controls
CAMPAIGN_STATUS = "ACTIVE"  # or "PAUSED"
ADSET_STATUS = "ACTIVE"     # or "PAUSED"
AD_STATUS = "ACTIVE"        # or "PAUSED"


# ---------------- DEBUG HELPERS ----------------
def _to_jsonable(obj):
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): _to_jsonable(v) for k, v in obj.items()}

    if hasattr(obj, "model_dump"):  # pydantic v2
        try:
            return obj.model_dump()
        except Exception:
            pass
    if hasattr(obj, "dict"):  # pydantic v1
        try:
            return obj.dict()
        except Exception:
            pass
    if hasattr(obj, "__dict__"):
        try:
            return {k: _to_jsonable(v) for k, v in obj.__dict__.items() if not k.startswith("_")}
        except Exception:
            pass
    return repr(obj)


def log_response(step: str, resp) -> None:
    print(f"\n---- {step} RESPONSE ----")
    try:
        print(json.dumps(_to_jsonable(resp), indent=2, ensure_ascii=False))
    except Exception:
        print(repr(resp))
    print("---- END RESPONSE ----\n")


def log_error(step: str, e: Exception) -> None:
    print(f"\n!!!! {step} FAILED !!!!")
    print(f"{type(e).__name__}: {e}")
    print("\n[traceback]")
    traceback.print_exc()
    print("!!!! END ERROR !!!!\n")


# ---------------- CONSOLE PROMPTS ----------------
def choose_asset_mode_console() -> str:
    print("\nChoose ad asset type:")
    print("  1) Reel / Video")
    print("  2) Image (single)")
    print("  3) Carousel (images)")
    print("  4) Carousel (mixed: images + video)")
    while True:
        raw = input("Selection (1 / 2 / 3 / 4): ").strip()
        if raw == "1":
            return "video"
        if raw == "2":
            return "image"
        if raw == "3":
            return "carousel_images"
        if raw == "4":
            return "carousel_mixed"
        print("Invalid selection. Please type 1, 2, 3, or 4.")


def prompt_path(default_path: str, label: str) -> str:
    print(f"\n{label} path (press Enter to keep default):")
    print(f"Default: {default_path}")
    raw = input("> ").strip()
    return raw if raw else default_path


def prompt_int(default_val: int, label: str) -> int:
    print(f"\n{label} (press Enter to keep default)")
    print(f"Default: {default_val}")
    raw = input("> ").strip()
    if not raw:
        return int(default_val)
    return int(raw)


def prompt_text(default_val: str, label: str) -> str:
    print(f"\n{label} (press Enter to keep default)")
    print(f"Default: {default_val}")
    raw = input("> ").strip()
    return raw if raw else default_val


def normalize_link(link: str) -> str:
    l = (link or "").strip()
    if not l:
        return DEFAULT_LINK_URL
    if l.startswith("http://") or l.startswith("https://"):
        return l
    return "https://" + l


def is_image_path(p: Path) -> bool:
    ext = p.suffix.lower()
    return ext in {".jpg", ".jpeg", ".png", ".webp"}


def is_video_path(p: Path) -> bool:
    ext = p.suffix.lower()
    return ext in {".mp4", ".mov", ".m4v"}


def prompt_carousel_paths(allow_video: bool) -> list[str]:
    print("\nCarousel setup:")
    if allow_video:
        print("Enter paths to images/videos. Leave empty to finish.")
        prompt_label = "Media path"
    else:
        print("Enter paths to images. Leave empty to finish.")
        prompt_label = "Image path"

    paths: list[str] = []
    while True:
        raw = input(f"{prompt_label}: ").strip()
        if not raw:
            break
        paths.append(raw)

    if len(paths) < 2:
        raise ValueError("Carousel requires at least 2 items.")
    return paths


def main() -> None:
    # ---------- Resolve meta_user_id / page_id / ig_actor_id from DB ----------
    reader = MetaTokenDbReader(database_url=DATABASE_URL, fernet_key=FERNET_KEY)

    meta_user = reader.get_latest_meta_user_for_client(CLIENT_ID)
    log_response("DB meta_user (latest)", meta_user)
    meta_user_id = (meta_user or {}).get("meta_user_id")
    if not meta_user_id:
        raise RuntimeError("No meta_user_id found for this client in DB (meta_user table).")

    meta_page = reader.get_latest_meta_page_for_client(CLIENT_ID)
    log_response("DB meta_page (latest)", meta_page)
    page_id = (meta_page or {}).get("page_id")
    if not page_id:
        raise RuntimeError("No page_id found for this client in DB (meta_page table).")

    ig_actor_id = reader.get_instagram_actor_id_for_client(CLIENT_ID)
    log_response("DB instagram_actor_id (latest)", {"ig_actor_id": ig_actor_id})
    if not ig_actor_id:
        raise RuntimeError(
            "No Instagram account linked to this client in DB (instagram_account table). "
            "Run token_uploader_console.py and ensure the selected Page is linked to an IG Business account."
        )

    # ---------- Init Ads manager ----------
    ads = AdsStairway(
        database_url=DATABASE_URL,
        encryption_key=FERNET_KEY,
        meta_user_id=str(meta_user_id),
        client_id=CLIENT_ID,
        page_id=str(page_id),
        instagram_actor_id=str(ig_actor_id),
        graph_version=GRAPH_API_VERSION,
    )

    # STEP 1
    try:
        print("STEP 1: Fetch ad accounts & generate campaigns")
        info = ads.get_ad_accounts(campaign_name=CAMPAIGN_NAME, objective=OBJECTIVE)
        log_response("STEP 1 get_ad_accounts()", info)
    except Exception as e:
        log_error("STEP 1", e)
        return

    # STEP 2
    try:
        print("STEP 2: Create campaign")
        campaign = ads.create_campaign_by_index(INDEX, status=CAMPAIGN_STATUS)
        log_response("STEP 2 create_campaign_by_index()", campaign)
    except Exception as e:
        log_error("STEP 2", e)
        return

    # ---------- Always prompt (budget/title/link) ----------
    daily_budget = prompt_int(1000, "Daily budget")
    title = prompt_text("Check this out!", "Ad title")
    link = prompt_text("youtube.com", "Redirect link")
    final_link = normalize_link(link)

    # ---------- Choose asset ----------
    mode = choose_asset_mode_console()
    # targeting: carousel is treated as "image" placement-wise; mixed carousel also uses image placements
    asset_type = "video" if mode == "video" else "image"
    log_response("CHOICE asset_type", {"mode": mode, "asset_type": asset_type})

    # STEP 3
    try:
        print("STEP 3: Create adset")
        # IMPORTANT:
        # - create_adset signature stays (index, status, asset_type)
        # - budget/title/link are stored on the adset object AFTER creation (no kwargs)
        adset = ads.create_adset(INDEX, status=ADSET_STATUS, asset_type=asset_type)

        # Store user-entered values on schema (local state)
        adset.daily_budget = int(daily_budget)
        adset.title = str(title)
        adset.link = str(final_link)

        log_response("STEP 3 create_adset()", adset)
    except Exception as e:
        log_error("STEP 3", e)
        return

    # =========================
    # VIDEO FLOW
    # =========================
    if mode == "video":
        try:
            chosen_video_path = prompt_path(VIDEO_PATH, "Video")
            video_path = Path(chosen_video_path)
            if not video_path.exists():
                raise FileNotFoundError(f"Video not found at {chosen_video_path}")
            print("STEP 4: Load video file")
            log_response("STEP 4 video_path", {"path": str(video_path), "size_bytes": video_path.stat().st_size})
        except Exception as e:
            log_error("STEP 4", e)
            return

        try:
            print("STEP 5: Save video + generate ngrok URLs")
            with open(video_path, "rb") as f:
                upload_file = UploadFile(filename=video_path.name, file=f)
                media = save_ad_media(upload_file)
            log_response("STEP 5 save_ad_media()", media)

            video_url = media.get("video_url")
            thumbnail_url = media.get("thumbnail_url") or ""
            if not video_url:
                raise ValueError("Ngrok video_url not returned correctly")
            print(f"Video URL: {video_url}")
            print(f"Thumbnail URL: {thumbnail_url}")
        except Exception as e:
            log_error("STEP 5", e)
            return

        try:
            print("STEP 6: Upload video to Meta")
            vid = ads.upload_ad_video(adset_index=INDEX, video_url=video_url)
            log_response("STEP 6 upload_ad_video()", {"video_id": vid})
        except Exception as e:
            log_error("STEP 6", e)
            return

        try:
            print("STEP 7: Create IG video ad creative + paid ad")
            ad_result = ads.create_paid_ig_ad(
                adset_index=INDEX,
                ad_name=title,
                thumbnail_url=thumbnail_url,
                status=AD_STATUS,
            )
            log_response("STEP 7 create_paid_ig_ad()", ad_result)
        except Exception as e:
            log_error("STEP 7", e)
            return

    # =========================
    # IMAGE FLOW (SINGLE)
    # =========================
    elif mode == "image":
        try:
            chosen_image_path = prompt_path(IMAGE_PATH, "Image")
            image_path = Path(chosen_image_path)
            if not image_path.exists():
                raise FileNotFoundError(f"Image not found at {chosen_image_path}")
            if not is_image_path(image_path):
                raise ValueError("Please provide an image file (.jpg/.png/.webp)")
            print("STEP 4: Load image file")
            log_response("STEP 4 image_path", {"path": str(image_path), "size_bytes": image_path.stat().st_size})
        except Exception as e:
            log_error("STEP 4", e)
            return

        try:
            print("STEP 5: Upload image to Meta (multipart local file)")
            image_hash = ads.upload_ad_image(adset_index=INDEX, image_path=str(image_path))
            log_response("STEP 5 upload_ad_image()", {"image_hash": image_hash})
        except Exception as e:
            log_error("STEP 5", e)
            return

        try:
            print("STEP 6: Create IG image ad creative + paid ad")
            ad_result = ads.create_paid_ig_image_ad(
                adset_index=INDEX,
                ad_name=title,
                status=AD_STATUS,
                link_url=final_link,
            )
            log_response("STEP 6 create_paid_ig_image_ad()", ad_result)
        except Exception as e:
            log_error("STEP 6", e)
            return

    # =========================
    # CAROUSEL FLOW (IMAGES ONLY)
    # =========================
    elif mode == "carousel_images":
        try:
            raw_paths = prompt_carousel_paths(allow_video=False)
            valid_paths: list[Path] = []
            for p in raw_paths:
                pp = Path(p)
                if not pp.exists():
                    raise FileNotFoundError(f"Carousel image not found: {p}")
                if not is_image_path(pp):
                    raise ValueError(f"Carousel images only. Not an image: {p}")
                valid_paths.append(pp)
            log_response("STEP 4 carousel_paths", [str(p) for p in valid_paths])
        except Exception as e:
            log_error("STEP 4", e)
            return

        try:
            print("STEP 5: Upload carousel images to Meta (multipart local files)")
            hashes: list[str] = []
            for i, p in enumerate(valid_paths, start=1):
                try:
                    h = ads.upload_ad_image(adset_index=INDEX, image_path=str(p))
                    hashes.append(h)
                    print(f"  uploaded {i}/{len(valid_paths)} -> {h}")
                except Exception as inner:
                    raise RuntimeError(f"Failed uploading carousel image {p.name}: {inner}") from inner
            log_response("STEP 5 carousel_image_hashes", hashes)
        except Exception as e:
            log_error("STEP 5", e)
            return

        try:
            print("STEP 6: Create IG carousel ad creative + paid ad")
            # NOTE: ads_stairway must implement create_paid_ig_carousel_ad(image_hashes=...)
            ad_result = ads.create_paid_ig_carousel_ad(
                adset_index=INDEX,
                ad_name=title,
                image_hashes=hashes,
                status=AD_STATUS,
                link_url=final_link,
            )
            log_response("STEP 6 create_paid_ig_carousel_ad()", ad_result)
        except Exception as e:
            log_error("STEP 6", e)
            return

    # =========================
    # CAROUSEL FLOW (MIXED: IMAGES + VIDEO)
    # =========================
    else:
        try:
            raw_paths = prompt_carousel_paths(allow_video=True)
            valid_paths: list[Path] = []
            for p in raw_paths:
                pp = Path(p)
                if not pp.exists():
                    raise FileNotFoundError(f"Carousel item not found: {p}")
                if not (is_image_path(pp) or is_video_path(pp)):
                    raise ValueError(f"Unsupported carousel item type (image/video only): {p}")
                valid_paths.append(pp)
            log_response("STEP 4 carousel_media_paths", [str(p) for p in valid_paths])
        except Exception as e:
            log_error("STEP 4", e)
            return

        try:
            print("STEP 5: Upload carousel media (images + video)")
            child_attachments: list[dict] = []

            for i, p in enumerate(valid_paths, start=1):
                try:
                    if is_image_path(p):
                        h = ads.upload_ad_image(adset_index=INDEX, image_path=str(p))
                        child_attachments.append(
                            {"name": f"Card {i}", "link": final_link, "image_hash": h}
                        )
                        print(f"  uploaded image {i}/{len(valid_paths)} -> {h}")

                    else:
                        # Upload video to Meta (must exist in AdsStairway; if not, add it or reuse upload_ad_video() via ngrok)
                        # Strategy: use save_ad_media to host video, then upload_ad_video(file_url)
                        with open(p, "rb") as f:
                            upload_file = UploadFile(filename=p.name, file=f)
                            media = save_ad_media(upload_file)

                        video_url = media.get("video_url")
                        thumb_url = media.get("thumbnail_url")
                        if not video_url:
                            raise RuntimeError("No video_url from save_ad_media")
                        if not thumb_url:
                            raise RuntimeError("No thumbnail_url from save_ad_media")

                        vid = ads.upload_ad_video(adset_index=INDEX, video_url=video_url)

                        # Upload thumb as an adimage so video card has image_hash
                        thumb_hash = ads.upload_ad_image(adset_index=INDEX, image_url=thumb_url)

                        child_attachments.append(
                            {
                                "name": f"Card {i}",
                                "link": final_link,
                                "video_id": vid,
                                "image_hash": thumb_hash,  # IMPORTANT: required for carousel cards
                            }
                        )
                        print(f"  uploaded video {i}/{len(valid_paths)} -> {vid} (thumb_hash={thumb_hash})")

                except Exception as inner:
                    raise RuntimeError(f"Failed uploading carousel media {p.name}: {inner}") from inner

            log_response("STEP 5 child_attachments", child_attachments)

        except Exception as e:
            log_error("STEP 5", e)
            return

        try:
            print("STEP 6: Create IG mixed carousel creative + paid ad")
            # NOTE: AdsStairway must implement create_paid_ig_mixed_carousel_ad(child_attachments=...)
            ad_result = ads.create_paid_ig_mixed_carousel_ad(
                adset_index=INDEX,
                ad_name=title,
                child_attachments=child_attachments,
                status=AD_STATUS,
                link_url=final_link,
            )
            log_response("STEP 6 create_paid_ig_mixed_carousel_ad()", ad_result)
        except Exception as e:
            log_error("STEP 6", e)
            return


if __name__ == "__main__":
    main()
