# app/routers/ngrok_media_manager.py
from __future__ import annotations

import os
import uuid
import shutil
import io

import cv2
from PIL import Image
from fastapi import APIRouter, UploadFile, HTTPException, Form
from dotenv import load_dotenv

from app.models.schemas import OrganicPost

load_dotenv()

# -------------------- Config --------------------
MEDIA_DIR = "media"
NGROK_URL = os.getenv("NGROK_URL")

if not NGROK_URL:
    raise RuntimeError(
        "Missing NGROK_URL in environment. Example: NGROK_URL=https://xxxx.ngrok-free.app"
    )

os.makedirs(MEDIA_DIR, exist_ok=True)

# -------------------- FastAPI Router --------------------
router = APIRouter(prefix="/api/media", tags=["Media"])

# -------------------- Pure Functions --------------------
def ensure_video_min_width(input_path: str, output_path: str, min_width: int = 500) -> None:
    cap = cv2.VideoCapture(input_path)
    if not cap.isOpened():
        raise HTTPException(status_code=500, detail="Cannot open video")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)

    if width <= 0 or height <= 0:
        cap.release()
        raise HTTPException(status_code=500, detail="Invalid video dimensions")

    if width >= min_width:
        cap.release()
        shutil.move(input_path, output_path)
        return

    scale = min_width / float(width)
    new_w = min_width
    new_h = int(round(height * scale))

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(output_path, fourcc, fps, (new_w, new_h))

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_LANCZOS4)
        writer.write(resized)

    cap.release()
    writer.release()
# app/routers/ngrok_media_manager.py



import os
import uuid
from pathlib import Path
from starlette.datastructures import UploadFile as StarletteUploadFile


MEDIA_DIR = Path("media")  # only if not already defined elsewhere
MEDIA_DIR.mkdir(parents=True, exist_ok=True)

PUBLIC_BASE_URL = os.getenv("NGROK_PUBLIC_URL") or os.getenv("NGROK_URL") or ""

def save_organic_image(title: str, upload_file: StarletteUploadFile):
    """
    Save an organic IMAGE to /media and return an OrganicPost-like object
    with .image_url populated.

    Mirrors save_organic_video behavior but for single images.
    """
    # keep extension from filename if possible
    ext = Path(upload_file.filename or "").suffix.lower() or ".jpg"
    filename = f"{uuid.uuid4()}{ext}"
    out_path = MEDIA_DIR / filename

    with open(out_path, "wb") as out:
        out.write(upload_file.file.read())

    image_url = f"{PUBLIC_BASE_URL}/media/{filename}" if PUBLIC_BASE_URL else f"/media/{filename}"

    # If your project expects an OrganicPost object, import and return it.
    # Otherwise return a dict and adapt the caller.
    from app.models.schemas import OrganicPost
    return OrganicPost(title=title, image_url=image_url)


def save_organic_video(title: str, video_file: UploadFile) -> OrganicPost:
    filename = f"{uuid.uuid4()}_{video_file.filename}"
    video_path = os.path.join(MEDIA_DIR, filename)

    try:
        video_file.file.seek(0)
    except Exception:
        pass

    with open(video_path, "wb") as buffer:
        shutil.copyfileobj(video_file.file, buffer)

    return OrganicPost(
        title=title,
        video_url=f"{NGROK_URL}/media/{filename}",
        creation_id=None,
        instagram_post_id=None,
    )


def save_ad_media(video_file: UploadFile) -> dict:
    """
    Ad VIDEO workflow:
      - save raw
      - ensure width >= 500
      - generate thumbnail png
      - return public URLs
    """
    raw_filename = f"raw_{uuid.uuid4()}_{video_file.filename}"
    final_filename = f"{uuid.uuid4()}_{video_file.filename}"

    raw_path = os.path.join(MEDIA_DIR, raw_filename)
    final_path = os.path.join(MEDIA_DIR, final_filename)

    try:
        video_file.file.seek(0)
    except Exception:
        pass

    with open(raw_path, "wb") as buffer:
        shutil.copyfileobj(video_file.file, buffer)

    ensure_video_min_width(raw_path, final_path, min_width=500)

    cap = cv2.VideoCapture(final_path)
    success, frame = cap.read()
    cap.release()

    if not success or frame is None:
        raise HTTPException(status_code=500, detail="Failed to extract thumbnail")

    h, w = frame.shape[:2]
    if w < 500:
        scale = 500 / float(w)
        frame = cv2.resize(frame, (500, int(h * scale)), interpolation=cv2.INTER_LANCZOS4)

    thumb_filename = f"thumb_{uuid.uuid4()}.png"
    thumb_path = os.path.join(MEDIA_DIR, thumb_filename)

    ok = cv2.imwrite(thumb_path, frame)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to write thumbnail image")

    return {
        "video_url": f"{NGROK_URL}/media/{final_filename}",
        "thumbnail_url": f"{NGROK_URL}/media/{thumb_filename}",
    }


def save_ad_image_media(
    image_file: UploadFile,
    target_size: tuple[int, int] = (1080, 1350),  # 4:5 feed
) -> dict:
    """
    Ad IMAGE workflow (Meta-safe):
      - read bytes
      - decode with PIL
      - convert to RGB (kills CMYK / alpha)
      - center-crop to target aspect
      - resize to target_size
      - save as baseline JPEG (progressive=False)
      - return public URL
    """
    try:
        image_file.file.seek(0)
    except Exception:
        pass

    raw = image_file.file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty image upload")

    try:
        img = Image.open(io.BytesIO(raw))
        img = img.convert("RGB")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid image: {e}")

    tw, th = target_size
    target_ratio = tw / th

    w, h = img.size
    if w <= 0 or h <= 0:
        raise HTTPException(status_code=400, detail="Invalid image dimensions")

    src_ratio = w / h

    if src_ratio > target_ratio:
        # too wide -> crop width
        new_w = int(h * target_ratio)
        left = max(0, (w - new_w) // 2)
        img = img.crop((left, 0, left + new_w, h))
    else:
        # too tall -> crop height
        new_h = int(w / target_ratio)
        top = max(0, (h - new_h) // 2)
        img = img.crop((0, top, w, top + new_h))

    img = img.resize((tw, th), Image.LANCZOS)

    filename = f"{uuid.uuid4()}.jpg"
    out_path = os.path.join(MEDIA_DIR, filename)

    # baseline JPEG is the key here
    img.save(out_path, format="JPEG", quality=92, optimize=True, progressive=False)

    return {"image_url": f"{NGROK_URL}/media/{filename}"}

# -------------------- FastAPI Endpoints --------------------
@router.post("/ngrok-upload-organic")
def ngrok_upload_organic(title: str = Form(...), video_file: UploadFile = ...):
    organic_post = save_organic_video(title, video_file)
    return {
        "message": "Organic video uploaded",
        "organic_post": organic_post.dict(),
    }


@router.post("/ngrok-upload-ad")
def ngrok_upload_ad(video_file: UploadFile):
    """
    Existing endpoint: VIDEO ads
    """
    media = save_ad_media(video_file)
    return {
        "message": "Ad media uploaded",
        "media": media,
    }


@router.post("/ngrok-upload-ad-image")
def ngrok_upload_ad_image(image_file: UploadFile):
    """
    New endpoint: IMAGE ads
    """
    media = save_ad_image_media(image_file)
    return {
        "message": "Ad image uploaded",
        "media": media,
    }
