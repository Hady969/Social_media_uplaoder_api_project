# app/utils/spaces_uploader.py
from __future__ import annotations

import os
import uuid
import mimetypes
from typing import BinaryIO, Optional, Literal

import boto3
from botocore.client import Config


OrganicKind = Literal["image", "video"]


class SpacesUploader:
    """
    DigitalOcean Spaces uploader wired to your env variable names.

    REQUIRED env (your names):
      DO_SPACE_NAME        (bucket/space name)          e.g. "aisocial-media"
      DO_SPACE_REGION      (region)                      e.g. "fra1"
      DO_SPACES_KEY        (access key)
      DO_SPACES_SECRET     (secret key)

    OPTIONAL env:
      DO_SPACES_ENDPOINT   e.g. "https://fra1.digitaloceanspaces.com"
                           (if missing, derived from DO_SPACE_REGION)
      DO_SPACES_CDN_BASE_URL e.g. "https://<bucket>.<region>.cdn.digitaloceanspaces.com"
                             OR your custom CDN domain

    Notes:
    - Returns PUBLIC URLs (no ngrok).
    - Use upload_organic_video/upload_organic_image for your organic pipeline.
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
        # ---- Read from your env names ----
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

        # Endpoint: allow explicit override; otherwise derive from region
        env_endpoint = (os.getenv("DO_SPACES_ENDPOINT") or "").strip()
        self.endpoint = (endpoint or env_endpoint or f"https://{self.region}.digitaloceanspaces.com").rstrip("/")

        # Optional CDN base URL
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

    def public_url_for_key(self, key: str) -> str:
        k = key.lstrip("/")
        if self.cdn_base_url:
            return f"{self.cdn_base_url}/{k}"
        # Works if your space is public and/or objects are public-read
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

        # Ensure start
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
