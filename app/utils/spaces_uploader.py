from __future__ import annotations

import os
import uuid
import shutil
import io
import tempfile
from pathlib import Path

import cv2
from PIL import Image
import boto3
from botocore.client import Config
from fastapi import UploadFile, HTTPException
from dotenv import load_dotenv
load_dotenv()


from app.models.schemas import OrganicPost


class SpacesMediaManager:
    """
    Drop-in replacement for ngrok_media_manager.
    Handles:
      - media adequacy
      - temp files
      - upload to DigitalOcean Spaces
      - returns public URLs

    Also includes an interactive console for manual testing from a terminal.
    """

    def __init__(self):
        self.space = os.environ["DO_SPACE_NAME"]
        self.region = os.environ["DO_SPACE_REGION"]

        session = boto3.Session()
        self.client = session.client(
            "s3",
            region_name=self.region,
            endpoint_url=f"https://{self.region}.digitaloceanspaces.com",
            aws_access_key_id=os.environ["DO_SPACES_KEY"],
            aws_secret_access_key=os.environ["DO_SPACES_SECRET"],
            config=Config(signature_version="s3v4"),
        )

    # -------------------- Infra --------------------
    def _upload(self, local_path: str, prefix: str, content_type: str) -> str:
        key = f"{prefix}/{uuid.uuid4().hex}{Path(local_path).suffix}"
        self.client.upload_file(
            local_path,
            self.space,
            key,
            ExtraArgs={"ACL": "public-read", "ContentType": content_type},
        )
        return f"https://{self.space}.{self.region}.digitaloceanspaces.com/{key}"

    def _safe_unlink(self, path: str | None):
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass

    # -------------------- Specs --------------------
    def ensure_video_min_width(self, input_path: str, output_path: str, min_width=500):
        cap = cv2.VideoCapture(input_path)
        if not cap.isOpened():
            raise HTTPException(status_code=500, detail="Cannot open video")

        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)

        if w <= 0 or h <= 0:
            cap.release()
            raise HTTPException(status_code=500, detail="Invalid video dimensions")

        if w >= min_width:
            cap.release()
            shutil.move(input_path, output_path)
            return

        scale = min_width / float(w)
        new_w, new_h = min_width, int(h * scale)

        writer = cv2.VideoWriter(
            output_path,
            cv2.VideoWriter_fourcc(*"mp4v"),
            fps,
            (new_w, new_h),
        )

        while True:
            ret, frame = cap.read()
            if not ret:
                break
            frame = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_LANCZOS4)
            writer.write(frame)

        cap.release()
        writer.release()

    # -------------------- Public API (same as ngrok) --------------------
    def save_organic_video(self, title: str, video_file: UploadFile) -> OrganicPost:
        ext = Path(video_file.filename or "").suffix or ".mp4"
        tmp = None

        try:
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as f:
                tmp = f.name
                shutil.copyfileobj(video_file.file, f)

            url = self._upload(tmp, "organic/videos", "video/mp4")
            return OrganicPost(title=title, video_url=url)

        finally:
            self._safe_unlink(tmp)

    def save_ad_media(self, video_file: UploadFile) -> dict:
        ext = Path(video_file.filename or "").suffix or ".mp4"
        raw = final = thumb = None

        try:
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as f:
                raw = f.name
                shutil.copyfileobj(video_file.file, f)

            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as f:
                final = f.name

            self.ensure_video_min_width(raw, final)

            cap = cv2.VideoCapture(final)
            ok, frame = cap.read()
            cap.release()
            if not ok or frame is None:
                raise HTTPException(status_code=500, detail="Thumbnail extraction failed")

            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                thumb = f.name
                ok2 = cv2.imwrite(thumb, frame)
                if not ok2:
                    raise HTTPException(status_code=500, detail="Failed to write thumbnail")

            return {
                "video_url": self._upload(final, "ads/videos", "video/mp4"),
                "thumbnail_url": self._upload(thumb, "ads/thumbnails", "image/png"),
            }

        finally:
            self._safe_unlink(raw)
            self._safe_unlink(final)
            self._safe_unlink(thumb)

    def save_ad_image_media(self, image_file: UploadFile) -> dict:
        out = None

        try:
            img = Image.open(image_file.file).convert("RGB")
            img = img.resize((1080, 1350), Image.LANCZOS)

            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
                out = f.name
                img.save(f, format="JPEG", quality=92, optimize=True, progressive=False)

            return {"image_url": self._upload(out, "ads/images", "image/jpeg")}

        finally:
            self._safe_unlink(out)

    # -------------------- Local console test helpers --------------------
    def upload_local_file(self, file_path: str, *, kind: str) -> dict:
        """
        kind:
          - "ad_image"      -> applies Meta-safe resize+JPEG
          - "ad_video"      -> ensures min width + thumbnail
          - "organic_video" -> uploads as-is
          - "raw"           -> uploads as-is to test/
        """
        p = Path(file_path)
        if not p.exists():
            raise FileNotFoundError(str(p))

        kind = kind.strip().lower()

        if kind == "raw":
            ct = "application/octet-stream"
            return {"url": self._upload(str(p), "test/raw", ct)}

        if kind == "organic_video":
            ct = "video/mp4"
            return {"video_url": self._upload(str(p), "organic/videos", ct)}

        if kind == "ad_video":
            # run the same pipeline as save_ad_media but from a local path
            raw = final = thumb = None
            ext = p.suffix or ".mp4"
            try:
                with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as f:
                    raw = f.name
                    shutil.copyfileobj(open(p, "rb"), f)

                with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as f:
                    final = f.name

                self.ensure_video_min_width(raw, final)

                cap = cv2.VideoCapture(final)
                ok, frame = cap.read()
                cap.release()
                if not ok or frame is None:
                    raise RuntimeError("Thumbnail extraction failed")

                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                    thumb = f.name
                    if not cv2.imwrite(thumb, frame):
                        raise RuntimeError("Failed to write thumbnail")

                return {
                    "video_url": self._upload(final, "ads/videos", "video/mp4"),
                    "thumbnail_url": self._upload(thumb, "ads/thumbnails", "image/png"),
                }
            finally:
                self._safe_unlink(raw)
                self._safe_unlink(final)
                self._safe_unlink(thumb)

        if kind == "ad_image":
            out = None
            try:
                img = Image.open(str(p)).convert("RGB")
                img = img.resize((1080, 1350), Image.LANCZOS)
                with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
                    out = f.name
                    img.save(f, format="JPEG", quality=92, optimize=True, progressive=False)
                return {"image_url": self._upload(out, "ads/images", "image/jpeg")}
            finally:
                self._safe_unlink(out)

        raise ValueError("Unknown kind. Use: raw, organic_video, ad_video, ad_image")

    def console(self) -> None:
        """
        Interactive console with guided choices.
        """
        MENU = {
            "1": ("Organic photo", "ad_image"),      # organic photo = same processing as ad_image
            "2": ("Organic video", "organic_video"),
            "3": ("Ad video", "ad_video"),
            "4": ("Ad photo", "ad_image"),
        }

        print("\nSpacesMediaManager console")
        print("-" * 40)

        while True:
            print("\nChoose media type:")
            for k, (label, _) in MENU.items():
                print(f"  {k}) {label}")
            print("  q) Quit")

            choice = input("\nSelect option> ").strip().lower()
            if choice in {"q", "quit", "exit"}:
                print("bye")
                return

            if choice not in MENU:
                print("Invalid choice. Try again.")
                continue

            label, kind = MENU[choice]
            path = input(f"Drop file path for {label}> ").strip().strip('"').strip("'")

            if path.lower() in {"q", "quit", "exit"}:
                print("bye")
                return

            try:
                result = self.upload_local_file(path, kind=kind)
                print("\nSUCCESS:")
                for k, v in result.items():
                    print(f"  {k}: {v}")
            except Exception as e:
                print(f"\nERROR: {e}")



if __name__ == "__main__":
    # Run this file directly:
    #   python -m app.utils.spaces_media_manager
    # or
    #   python app/utils/spaces_media_manager.py
    SpacesMediaManager().console()
