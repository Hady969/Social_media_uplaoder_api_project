from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.routers import ig_campaign
from app.routers.ngrok_media_manager import router as media_router
from app.routers.organic_poster import router as organic_router
from app.meta_oauth import router as meta_oauth_router
from app.routers.ad_image_uploader import router as ad_image_router

app = FastAPI()

# ---------------- Static files (ABSOLUTE PATH) ----------------
BASE_DIR = Path(__file__).resolve().parent.parent  # .../aisocialbackend
MEDIA_DIR = BASE_DIR / "media"
MEDIA_DIR.mkdir(exist_ok=True)

app.mount("/media", StaticFiles(directory=str(MEDIA_DIR)), name="media")

# ---------------- Routers ----------------
app.include_router(ig_campaign.router, prefix="/ig")
app.include_router(media_router)
app.include_router(organic_router)

# Meta OAuth callback (no prefix)
app.include_router(meta_oauth_router)

app.include_router(ad_image_router)
