from dotenv import load_dotenv
load_dotenv()  # loads .env from current working directory by default

from fastapi import FastAPI
from app.routers.Oauth_routers.oauth_meta_callback import router as meta_oauth_callback_router

app = FastAPI()
app.include_router(meta_oauth_callback_router)
