from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv
import requests
import os

load_dotenv()

IG_USER_ID = os.getenv("IG_USER_ID")
META_APP_ID = os.getenv("META_APP_ID")
META_APP_SECRET = os.getenv("META_APP_SECRET")
META_USER_ACCESS_TOKEN = os.getenv("META_USER_ACCESS_TOKEN")

app = FastAPI(title="Instagram Reels API")


class InstagramAccount:
    def __init__(self, ig_user_id, app_id, app_secret, user_access_token):
        self.ig_user_id = ig_user_id
        self.app_id = app_id
        self.app_secret = app_secret
        self.user_access_token = user_access_token
        self.long_lived_token = None

    def get_long_lived_access_token(self):
        url = "https://graph.facebook.com/v17.0/oauth/access_token"
        params = {
            "grant_type": "fb_exchange_token",
            "client_id": self.app_id,
            "client_secret": self.app_secret,
            "fb_exchange_token": self.user_access_token
        }
        response = requests.get(url, params=params).json()
        if "access_token" not in response:
            raise Exception(response)
        self.long_lived_token = response["access_token"]
        return self.long_lived_token

    def fetch_reels(self, ig_username: str, limit: int):
        token = self.long_lived_token or self.user_access_token
        media_fields = (
            "thumbnail_url,media_type,media_product_type,timestamp,like_count,"
            "comments_count,media_url,permalink,caption"
        )
        required_param = (
            f"{{name,website,biography,followers_count,media_count,profile_picture_url,"
            f"media.limit({limit}){{{media_fields}}}}}"
        )
        url = (
            f"https://graph.facebook.com/v17.0/{self.ig_user_id}"
            f"?fields=business_discovery.username({ig_username}){required_param}"
            f"&access_token={token}"
        )
        response = requests.get(url)
        metadata = response.json()
        try:
            media_items = metadata["business_discovery"]["media"]["data"]
            reels = [
                item for item in media_items
                if item.get("media_type") == "VIDEO" and item.get("media_product_type") == "REELS"
            ]
        except KeyError:
            reels = []
        return reels

    # ✅ New helper method
    def print_reels(self, ig_username: str, limit: int = 10):
        reels = self.fetch_reels(ig_username, limit)
        if not reels:
            print(f"No reels found for {ig_username}")
            return
        for idx, r in enumerate(reels, 1):
            print(f"Reel {idx}:")
            print(f"  Caption: {r.get('caption')}")
            print(f"  URL: {r.get('media_url')}")
            print(f"  Permalink: {r.get('permalink')}")
            print(f"  Likes: {r.get('like_count')}, Comments: {r.get('comments_count')}")
            print("-" * 40)
