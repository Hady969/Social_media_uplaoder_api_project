# app/models/spaces_uploader.py
from __future__ import annotations

import io
import os
import uuid
import mimetypes
import tempfile
from pathlib import Path
from typing import Any, BinaryIO, Dict, Optional, Literal, Tuple

import boto3
from botocore.client import Config

# Optional dependencies (graceful)
try:
    import cv2  # type: ignore
    HAS_CV2 = True
except Exception:
    HAS_CV2 = False

try:
    from PIL import Image  # type: ignore
    HAS_PIL = True
except Exception:
    HAS_PIL = False


OrganicKind = Literal["image", "video"]


class SpacesUploader:
    """
    DigitalOcean Spaces uploader (S3-compatible) + compatibility methods for your pipelines.

    This file is designed to make BOTH your IG and FB console pipelines work with minimal edits.

    Provides:
      - upload_fileobj(...) -> public URL
      - upload_organic_video / upload_organic_image
      - save_ad_media(upload_file) -> dict like your pipelines expect:
            video: {"video_url": ..., "thumbnail_url": ...}
            image: {"image_url": ...}
      - save_ad_image(upload_file) -> {"image_url": ...} (Meta-safe baseline JPEG if PIL available)
      - save_ad_video(upload_file) -> {"video_url": ..., "thumbnail_url": ...} (thumb if cv2 available)

    REQUIRED env (your names):
      DO_SPACE_NAME        (bucket/space name)          e.g. "aisocial-media"
      DO_SPACE_REGION      (region)                      e.g. "fra1"
      DO_SPACES_KEY        (access key)
      DO_SPACES_SECRET     (secret key)

    OPTIONAL env:
      DO_SPACES_ENDPOINT   e.g. "https://fra1.digitaloceanspaces.com"
      DO_SPACES_CDN_BASE_URL e.g. "https://<bucket>.<region>.cdn.digitaloceanspaces.com"
                             OR your custom CDN domain
    """

    def __init__(
        self,
        *,
        space_name: Optional[str] = None,
        region: Optional[str] = None,
        key: Optional[str] = None,
        secret: Optional[str] = None,
        endpoint: Optional[str] = None,
        cdn_base_url: Optional[str] = None,
    ) -> None:
        # ---- Read from env (or override) ----
        self.bucket = (space_name or os.getenv("DO_SPACE_NAME") or "").strip()
        self.region = (region or os.getenv("DO_SPACE_REGION") or "").strip()
        self.key = (key or os.getenv("DO_SPACES_KEY") or "").strip()
        self.secret = (secret or os.getenv("DO_SPACES_SECRET") or "").strip()

        if not self.bucket:
            raise RuntimeError("Missing DO_SPACE_NAME (bucket/space name).")
        if not self.region:
            raise RuntimeError("Missing DO_SPACE_REGION (e.g. fra1).")
        if not self.key:
            raise RuntimeError("Missing DO_SPACES_KEY.")
        if not self.secret:
            raise RuntimeError("Missing DO_SPACES_SECRET.")

        env_endpoint = (os.getenv("DO_SPACES_ENDPOINT") or "").strip()
        self.endpoint = (endpoint or env_endpoint or f"https://{self.region}.digitaloceanspaces.com").rstrip("/")

        self.cdn_base_url = (cdn_base_url or os.getenv("DO_SPACES_CDN_BASE_URL") or "").strip().rstrip("/")

        # ---- boto3 client ----
        session = boto3.session.Session()
        self.s3 = session.client(
            "s3",
            region_name=self.region,
            endpoint_url=self.endpoint,
            aws_access_key_id=self.key,
            aws_secret_access_key=self.secret,
            config=Config(signature_version="s3v4"),
        )

    # ----------------- Core Upload -----------------
    def public_url_for_key(self, key: str) -> str:
        k = key.lstrip("/")
        if self.cdn_base_url:
            return f"{self.cdn_base_url}/{k}"
        return f"{self.endpoint}/{self.bucket}/{k}"

    def upload_fileobj(
        self,
        fileobj: BinaryIO,
        *,
        filename: str,
        folder: str,
        content_type: Optional[str] = None,
        acl: str = "public-read",
    ) -> str:
        ext = os.path.splitext(filename)[1].lower()
        guessed_type = mimetypes.types_map.get(ext)
        ct = content_type or guessed_type or "application/octet-stream"

        safe_folder = folder.strip("/")
        key = f"{safe_folder}/{uuid.uuid4().hex}{ext}"

        try:
            fileobj.seek(0)
        except Exception:
            pass

        self.s3.upload_fileobj(
            Fileobj=fileobj,
            Bucket=self.bucket,
            Key=key,
            ExtraArgs={"ACL": acl, "ContentType": ct},
        )
        return self.public_url_for_key(key)

    # ----------------- ORGANIC HELPERS -----------------
    def upload_organic(
        self,
        *,
        fileobj: BinaryIO,
        filename: str,
        kind: OrganicKind,
        content_type: Optional[str] = None,
    ) -> str:
        if kind == "video":
            folder = "organic/videos"
            default_ct = "video/mp4"
        else:
            folder = "organic/images"
            default_ct = "image/jpeg"

        return self.upload_fileobj(
            fileobj=fileobj,
            filename=filename,
            folder=folder,
            content_type=content_type or default_ct,
        )

    def upload_organic_video(self, *, fileobj: BinaryIO, filename: str, content_type: Optional[str] = None) -> str:
        return self.upload_organic(fileobj=fileobj, filename=filename, kind="video", content_type=content_type)

    def upload_organic_image(self, *, fileobj: BinaryIO, filename: str, content_type: Optional[str] = None) -> str:
        return self.upload_organic(fileobj=fileobj, filename=filename, kind="image", content_type=content_type)

    # ----------------- Compatibility / Ads -----------------
    def save_ad_media(self, upload_file: Any) -> Dict[str, Optional[str]]:
        """
        Single entrypoint used by your console pipelines:
          media = media_mgr.save_ad_media(upload_file)

        Returns:
          - video: {"video_url": str, "thumbnail_url": str|None, "image_url": None}
          - image: {"image_url": str, "video_url": None, "thumbnail_url": None}
        """
        filename = getattr(upload_file, "filename", None) or "upload.bin"
        fileobj = getattr(upload_file, "file", None)
        if fileobj is None:
            raise ValueError("save_ad_media expected upload_file.file (BinaryIO)")

        ext = Path(filename).suffix.lower()

        if ext in {".mp4", ".mov", ".m4v"}:
            return self.save_ad_video(upload_file)

        if ext in {".jpg", ".jpeg", ".png", ".webp"}:
            return self.save_ad_image(upload_file)

        raise ValueError(f"Unsupported ad media type: {ext}")

    def save_ad_video(
        self,
        upload_file: Any,
        *,
        extract_thumbnail: bool = True,
        min_width: int = 500,
    ) -> Dict[str, Optional[str]]:
        """
        Upload video to Spaces under ads/videos, optionally generate thumbnail under ads/thumbnails.
        - If OpenCV not available, thumbnail_url will be None.
        """
        filename = getattr(upload_file, "filename", None) or "video.mp4"
        fileobj = getattr(upload_file, "file", None)
        if fileobj is None:
            raise ValueError("save_ad_video expected upload_file.file (BinaryIO)")

        # Upload video
        video_url = self.upload_fileobj(
            fileobj=fileobj,
            filename=filename,
            folder="ads/videos",
            content_type="video/mp4",
        )

        thumbnail_url: Optional[str] = None
        if extract_thumbnail and HAS_CV2:
            try:
                thumb_bytes = self._extract_first_frame_bytes(fileobj, filename, min_width=min_width)
                if thumb_bytes:
                    thumbnail_url = self.upload_fileobj(
                        fileobj=io.BytesIO(thumb_bytes),
                        filename="thumb.jpg",
                        folder="ads/thumbnails",
                        content_type="image/jpeg",
                    )
            except Exception:
                thumbnail_url = None

        return {"video_url": video_url, "thumbnail_url": thumbnail_url, "image_url": None}

    def save_ad_image(
        self,
        upload_file: Any,
        *,
        target_size: Tuple[int, int] = (1080, 1350),  # 4:5
    ) -> Dict[str, Optional[str]]:
        """
        Upload image to Spaces under ads/images.
        - If PIL exists: converts to RGB, center-crops to 4:5, resizes, saves baseline JPEG (Meta-safe).
        - Otherwise uploads as-is.
        """
        filename = getattr(upload_file, "filename", None) or "image.jpg"
        fileobj = getattr(upload_file, "file", None)
        if fileobj is None:
            raise ValueError("save_ad_image expected upload_file.file (BinaryIO)")

        ext = Path(filename).suffix.lower()

        # If no PIL, just upload original bytes
        if not HAS_PIL:
            return {
                "image_url": self.upload_fileobj(
                    fileobj=fileobj,
                    filename=filename,
                    folder="ads/images",
                    content_type=None,
                ),
                "video_url": None,
                "thumbnail_url": None,
            }

        # Read bytes then process
        try:
            try:
                fileobj.seek(0)
            except Exception:
                pass
            raw = fileobj.read()
        finally:
            # reset for any future reuse
            try:
                fileobj.seek(0)
            except Exception:
                pass

        if not raw:
            raise ValueError("Empty image upload")

        img = Image.open(io.BytesIO(raw))
        img = img.convert("RGB")

        tw, th = target_size
        target_ratio = tw / th

        w, h = img.size
        if w <= 0 or h <= 0:
            raise ValueError("Invalid image dimensions")

        src_ratio = w / h

        # center-crop
        if src_ratio > target_ratio:
            new_w = int(h * target_ratio)
            left = max(0, (w - new_w) // 2)
            img = img.crop((left, 0, left + new_w, h))
        else:
            new_h = int(w / target_ratio)
            top = max(0, (h - new_h) // 2)
            img = img.crop((0, top, w, top + new_h))

        img = img.resize((tw, th), Image.LANCZOS)

        out = io.BytesIO()
        img.save(out, format="JPEG", quality=92, optimize=True, progressive=False)
        out.seek(0)

        image_url = self.upload_fileobj(
            fileobj=out,
            filename="processed.jpg",  # key is randomized anyway
            folder="ads/images",
            content_type="image/jpeg",
        )
        return {"image_url": image_url, "video_url": None, "thumbnail_url": None}

    # ----------------- Internal (OpenCV thumbnail) -----------------
    def _extract_first_frame_bytes(self, fileobj: BinaryIO, filename: str, *, min_width: int = 500) -> Optional[bytes]:
        """
        Writes the uploaded video stream to a temp file, reads first frame, returns JPEG bytes.
        Requires OpenCV. Best-effort; returns None on failure.
        """
        if not HAS_CV2:
            return None

        suffix = Path(filename).suffix.lower() or ".mp4"
        tmp_vid = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp_vid_path = tmp_vid.name
        tmp_vid.close()

        try:
            # write stream to temp file
            try:
                fileobj.seek(0)
            except Exception:
                pass
            with open(tmp_vid_path, "wb") as f:
                f.write(fileobj.read())

            # reset stream for caller
            try:
                fileobj.seek(0)
            except Exception:
                pass

            cap = cv2.VideoCapture(tmp_vid_path)  # type: ignore[name-defined]
            ok, frame = cap.read()
            cap.release()
            if not ok or frame is None:
                return None

            h, w = frame.shape[:2]
            if w > 0 and w < min_width:
                scale = min_width / float(w)
                frame = cv2.resize(  # type: ignore[name-defined]
                    frame,
                    (min_width, int(round(h * scale))),
                    interpolation=cv2.INTER_LANCZOS4,  # type: ignore[name-defined]
                )

            ok2, jpg = cv2.imencode(".jpg", frame)  # type: ignore[name-defined]
            if not ok2:
                return None
            return bytes(jpg.tobytes())
        finally:
            try:
                os.remove(tmp_vid_path)
            except Exception:
                pass


class SpacesMediaManager(SpacesUploader):
    """
    Backward-compatible alias.
    If any pipeline imports SpacesMediaManager, it will still work.
    """
    pass
