# app/routers/main_fb_ad_pipeline.py
from __future__ import annotations

import os
import json
import traceback
from pathlib import Path
import tempfile
from typing import Any

from fastapi import UploadFile
from dotenv import load_dotenv

from app.models.fb_ads_stairway import FbAdsStairway
from app.routers.DB_helpers.meta_token_db_reader import MetaTokenDbReader
from app.models.spaces_uploader import SpacesUploader

load_dotenv()

# ---------------- CONFIG ----------------
VIDEO_PATH = r"C:\Users\User\Desktop\Ig_Reels\example.mp4"
IMAGE_PATH = r"C:\Users\User\Pictures\example.jpg"

CLIENT_ID = os.environ["CLIENT_ID"]
GRAPH_API_VERSION = os.getenv("GRAPH_API_VERSION", "v17.0")

DATABASE_URL = os.environ["DATABASE_URL"]
FERNET_KEY = os.environ["TOKEN_ENCRYPTION_KEY"]

CAMPAIGN_NAME = "Automated FB Campaign_DB"
OBJECTIVE = "OUTCOME_AWARENESS"
INDEX = 0

DEFAULT_LINK_URL = os.getenv("DEFAULT_AD_LINK_URL", "https://www.facebook.com/")

CAMPAIGN_STATUS = "ACTIVE"
ADSET_STATUS = "ACTIVE"
AD_STATUS = "ACTIVE"


# ---------------- HELPERS ----------------
def _to_jsonable(obj: Any):
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): _to_jsonable(v) for k, v in obj.items()}
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump()
        except Exception:
            pass
    if hasattr(obj, "dict"):
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


def dbg(tag: str, payload) -> None:
    try:
        print(f"\n[FB PIPELINE DBG] {tag}")
        print(json.dumps(_to_jsonable(payload), indent=2, ensure_ascii=False))
        print("[/FB PIPELINE DBG]\n")
    except Exception:
        print(f"\n[FB PIPELINE DBG] {tag}: {payload!r}\n[/FB PIPELINE DBG]\n")


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


def choose_asset_mode_console() -> str:
    print("\nChoose Facebook ad asset type:")
    print("  1) Video")
    print("  2) Image (single)")
    print("  3) Carousel (images)")
    print("  4) Carousel (mixed: images + videos)")
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
    return p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}


def is_video_path(p: Path) -> bool:
    return p.suffix.lower() in {".mp4", ".mov", ".m4v"}


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


def download_url_to_tempfile(url: str, suffix: str = ".jpg", timeout_s: int = 60, retries: int = 3) -> str:
    """
    Robust thumbnail download (avoids urllib ContentTooShortError).
    """
    import time
    import requests

    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp_path = tmp.name
        tmp.close()

        try:
            with requests.get(url, stream=True, timeout=timeout_s) as r:
                r.raise_for_status()
                with open(tmp_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 256):
                        if chunk:
                            f.write(chunk)

            if Path(tmp_path).stat().st_size < 10_000:
                raise RuntimeError(f"Downloaded thumbnail too small: {Path(tmp_path).stat().st_size} bytes")

            return tmp_path

        except Exception as e:
            last_err = e
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except Exception:
                pass
            if attempt < retries:
                time.sleep(0.8 * attempt)

    raise RuntimeError(f"Failed to download thumbnail after {retries} attempts: {last_err}")


# ---------------- MAIN ----------------
def main() -> None:
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

    ads = FbAdsStairway(
        database_url=DATABASE_URL,
        encryption_key=FERNET_KEY,
        meta_user_id=str(meta_user_id),
        client_id=CLIENT_ID,
        page_id=str(page_id),
        graph_version=GRAPH_API_VERSION,
    )

    # IMPORTANT: this is "spaces" (not media_mgr), so no more "spaces is not defined"
    spaces = SpacesUploader()

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

    daily_budget = prompt_int(1000, "Daily budget")
    title = prompt_text("Check this out!", "Ad title")
    body = prompt_text("Discover more.", "Primary text (FB caption)")
    link = prompt_text("facebook.com", "Redirect link")
    final_link = normalize_link(link)

    mode = choose_asset_mode_console()

    # carousel modes still use "image" for adset creation in your current stairway
    asset_type = "video" if mode == "video" else "image"
    log_response("CHOICE asset_type", {"mode": mode, "asset_type": asset_type})

    # STEP 3
    try:
        print("STEP 3: Create adset (Facebook placements)")
        adset = ads.create_adset(
            INDEX,
            status=ADSET_STATUS,
            asset_type=asset_type,
            platform_mode="facebook",
        )

        adset.daily_budget = int(daily_budget)
        adset.title = str(title)
        adset.link = str(final_link)

        log_response("STEP 3 create_adset()", adset)
        dbg("ADSET_AFTER_MUTATION", adset)
    except Exception as e:
        log_error("STEP 3", e)
        return

    # =========================
    # VIDEO FLOW (FACEBOOK)
    # =========================
    if mode == "video":
        try:
            chosen_video_path = prompt_path(VIDEO_PATH, "Video")
            video_path = Path(chosen_video_path)
            if not video_path.exists():
                raise FileNotFoundError(f"Video not found at {chosen_video_path}")
            if not is_video_path(video_path):
                raise ValueError("Please provide a video file (.mp4/.mov/.m4v)")
            log_response("STEP 4 video_path", {"path": str(video_path), "size_bytes": video_path.stat().st_size})
        except Exception as e:
            log_error("STEP 4", e)
            return

        try:
            print("STEP 5: Save video + generate Spaces URLs")
            with open(video_path, "rb") as f:
                upload_file = UploadFile(filename=video_path.name, file=f)
                media = spaces.save_ad_media(upload_file)
            log_response("STEP 5 spaces.save_ad_media()", media)

            video_url = media.get("video_url")
            thumbnail_url = media.get("thumbnail_url") or ""
            if not video_url:
                raise ValueError("Spaces video_url not returned correctly")
            if not thumbnail_url:
                print("[WARN] thumbnail_url missing from SpacesUploader; FB video creative will fail without it.")
        except Exception as e:
            log_error("STEP 5", e)
            return

        try:
            print("STEP 6: Upload video to Meta (hosted URL)")
            vid = ads.upload_ad_video(adset_index=INDEX, video_url=video_url)
            log_response("STEP 6 upload_ad_video()", {"video_id": vid})
        except Exception as e:
            log_error("STEP 6", e)
            return

        try:
            print("STEP 7: Create Facebook video creative + paid ad")
            # NOTE: your FbAdsStairway.create_paid_fb_video_ad MUST put image_url or image_hash in video_data
            ad_result = ads.create_paid_fb_video_ad(
                adset_index=INDEX,
                ad_name=title,
                primary_text=body,
                link_url=final_link,
                video_id=str(vid),
                thumbnail_url=thumbnail_url,
                status=AD_STATUS,
            )
            log_response("STEP 7 create_paid_fb_video_ad()", ad_result)
        except Exception as e:
            log_error("STEP 7", e)
            return

    # =========================
    # IMAGE FLOW (FACEBOOK)
    # =========================
    elif mode == "image":
        try:
            chosen_image_path = prompt_path(IMAGE_PATH, "Image")
            image_path = Path(chosen_image_path)
            if not image_path.exists():
                raise FileNotFoundError(f"Image not found at {chosen_image_path}")
            if not is_image_path(image_path):
                raise ValueError("Please provide an image file (.jpg/.png/.webp)")
            log_response("STEP 4 image_path", {"path": str(image_path), "size_bytes": image_path.stat().st_size})
        except Exception as e:
            log_error("STEP 4", e)
            return

        try:
            print("STEP 5: Upload image to Meta")
            image_hash = ads.upload_ad_image(adset_index=INDEX, image_path=str(image_path))
            log_response("STEP 5 upload_ad_image()", {"image_hash": image_hash})
        except Exception as e:
            log_error("STEP 5", e)
            return

        try:
            print("STEP 6: Create Facebook image creative + paid ad")
            ad_result = ads.create_paid_fb_image_ad(
                adset_index=INDEX,
                ad_name=title,
                primary_text=body,
                link_url=final_link,
                image_hash=str(image_hash),
                status=AD_STATUS,
            )
            log_response("STEP 6 create_paid_fb_image_ad()", ad_result)
        except Exception as e:
            log_error("STEP 6", e)
            return

    # =========================
    # CAROUSEL FLOW (FACEBOOK, IMAGES ONLY)
    # =========================
    elif mode == "carousel_images":
        try:
            raw_paths = prompt_carousel_paths(allow_video=False)
            valid_image_paths: list[Path] = []
            for p in raw_paths:
                pp = Path(p)
                if not pp.exists():
                    raise FileNotFoundError(f"Carousel image not found: {p}")
                if not is_image_path(pp):
                    raise ValueError(f"Carousel images only. Not an image: {p}")
                valid_image_paths.append(pp)

            log_response("STEP 4 carousel_paths", [str(p) for p in valid_image_paths])
            dbg("CAROUSEL_IMAGE_PATHS", [str(p) for p in valid_image_paths])
        except Exception as e:
            log_error("STEP 4", e)
            return

        try:
            print("STEP 5: Upload carousel images to Meta")
            hashes = ads.upload_ad_images(adset_index=INDEX, image_paths=[str(p) for p in valid_image_paths])
            log_response("STEP 5 carousel_image_hashes", hashes)
        except Exception as e:
            log_error("STEP 5", e)
            return

        try:
            print("STEP 6: Create Facebook carousel creative + paid ad")
            child_attachments = [{"name": f"Card {i}", "link": final_link, "image_hash": h} for i, h in enumerate(hashes, start=1)]
            dbg("FB_CAROUSEL_CREATE_INPUT", {"child_attachments": child_attachments, "link_url": final_link})

            ad_result = ads.create_paid_fb_homogeneous_carousel_ad(
                adset_index=INDEX,
                ad_name=title,
                primary_text=body,
                link_url=final_link,
                child_attachments=child_attachments,
                status=AD_STATUS,
            )
            log_response("STEP 6 create_paid_fb_homogeneous_carousel_ad()", ad_result)
        except Exception as e:
            log_error("STEP 6", e)
            return

    # =========================
    # CAROUSEL FLOW (FACEBOOK, MIXED: IMAGES + VIDEO)
    # =========================
    else:
        try:
            raw_paths = prompt_carousel_paths(allow_video=True)
            valid_media_paths: list[Path] = []
            for p in raw_paths:
                pp = Path(p)
                if not pp.exists():
                    raise FileNotFoundError(f"Carousel item not found: {p}")
                if not (is_image_path(pp) or is_video_path(pp)):
                    raise ValueError(f"Unsupported carousel item type (image/video only): {p}")
                valid_media_paths.append(pp)

            log_response("STEP 4 carousel_media_paths", [str(p) for p in valid_media_paths])
            dbg("FB_CAROUSEL_MEDIA_PATHS", [str(p) for p in valid_media_paths])
        except Exception as e:
            log_error("STEP 4", e)
            return

        try:
            print("STEP 5: Upload carousel media (images + video)")
            child_attachments: list[dict] = []

            for i, p in enumerate(valid_media_paths, start=1):
                if is_image_path(p):
                    h = ads.upload_ad_image(adset_index=INDEX, image_path=str(p))
                    child_attachments.append({"name": f"Card {i}", "link": final_link, "image_hash": h})
                    dbg("FB_CAROUSEL_CHILD_ADD_IMAGE", {"i": i, "path": str(p), "image_hash": h})
                else:
                    # video -> Spaces upload -> hosted URL -> Meta upload
                    with open(p, "rb") as f:
                        upload_file = UploadFile(filename=p.name, file=f)
                        media = spaces.save_ad_media(upload_file)

                    video_url = media.get("video_url")
                    thumb_url = media.get("thumbnail_url")
                    if not video_url:
                        raise RuntimeError("No video_url from spaces.save_ad_media")
                    if not thumb_url:
                        raise RuntimeError("No thumbnail_url from spaces.save_ad_media")

                    vid = ads.upload_ad_video(adset_index=INDEX, video_url=video_url)

                    # robust thumbnail download -> upload thumb -> image_hash
                    thumb_tmp_path = download_url_to_tempfile(thumb_url, suffix=".jpg")
                    thumb_hash = ads.upload_ad_image(adset_index=INDEX, image_path=thumb_tmp_path)

                    child_attachments.append(
                        {"name": f"Card {i}", "link": final_link, "video_id": vid, "image_hash": thumb_hash}
                    )
                    dbg(
                        "FB_CAROUSEL_CHILD_ADD_VIDEO",
                        {"i": i, "path": str(p), "video_id": vid, "thumb_hash": thumb_hash},
                    )

            log_response("STEP 5 child_attachments", child_attachments)
            dbg("FB_CAROUSEL_CHILD_ATTACHMENTS_FINAL", child_attachments)
        except Exception as e:
            log_error("STEP 5", e)
            return

        try:
            print("STEP 6: Create Facebook mixed carousel creative + paid ad")
            # REQUIRED: implement ads.create_paid_fb_mixed_carousel_ad(...) in FbAdsStairway
            ad_result = ads.create_paid_fb_mixed_carousel_ad(
                adset_index=INDEX,
                ad_name=title,
                primary_text=body,
                link_url=final_link,
                child_attachments=child_attachments,
                status=AD_STATUS,
            )
            log_response("STEP 6 create_paid_fb_mixed_carousel_ad()", ad_result)
        except Exception as e:
            log_error("STEP 6", e)
            return


if __name__ == "__main__":
    main()
