from fastapi import APIRouter, UploadFile, HTTPException
from dotenv import load_dotenv
import os

from app.models.ads_stairway import AdsStairway
from app.routers.ngrok_media_manager import save_ad_image_media
from app.routers.meta_token_db_reader import MetaTokenDbReader

load_dotenv()

router = APIRouter(prefix="/api/ads", tags=["Ads Images"])

DATABASE_URL = os.environ["DATABASE_URL"]
FERNET_KEY = os.environ["TOKEN_ENCRYPTION_KEY"]
CLIENT_ID = os.environ["CLIENT_ID"]
GRAPH_API_VERSION = os.getenv("GRAPH_API_VERSION", "v17.0")


@router.post("/upload-image-to-meta")
def upload_ad_image_to_meta(image_file: UploadFile):
    """
    Full pipeline:
      1) Save + re-encode image (Meta-safe JPEG)
      2) Serve via ngrok
      3) Upload to Meta AdImages
      4) Return Meta image_hash
    """

    # ---- Resolve IDs from DB ----
    reader = MetaTokenDbReader(DATABASE_URL, FERNET_KEY)

    meta_user = reader.get_latest_meta_user_for_client(CLIENT_ID)
    meta_page = reader.get_latest_meta_page_for_client(CLIENT_ID)
    ig_actor_id = reader.get_instagram_actor_id_for_client(CLIENT_ID)

    if not meta_user or not meta_page or not ig_actor_id:
        raise HTTPException(status_code=400, detail="Missing Meta bindings in DB")

    # ---- Init Ads layer ----
    ads = AdsStairway(
        database_url=DATABASE_URL,
        encryption_key=FERNET_KEY,
        meta_user_id=str(meta_user["meta_user_id"]),
        client_id=CLIENT_ID,
        page_id=str(meta_page["page_id"]),
        instagram_actor_id=str(ig_actor_id),
        graph_version=GRAPH_API_VERSION,
    )

    # ---- Step 1: save image + ngrok URL ----
    media = save_ad_image_media(image_file)
    image_url = media["image_url"]

    # ---- Step 2: upload to Meta ----
    # fake adset index (image upload is ad-account scoped)
    ads.adsets.append(type("Tmp", (), {})())
    image_hash = ads.upload_ad_image(adset_index=0, image_url=image_url)

    return {
        "status": "success",
        "image_url": image_url,
        "image_hash": image_hash,
    }

