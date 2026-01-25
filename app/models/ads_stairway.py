from __future__ import annotations

import os
import json
import mimetypes
import requests
from typing import List, Literal, Optional

from app.models import schemas
from app.routers.meta_token_db_reader import MetaTokenDbReader

MetaStatus = Literal["ACTIVE", "PAUSED"]
AssetType = Literal["video", "image"]


def normalize_status(status: MetaStatus) -> str:
    s = status.strip().upper()
    if s not in {"ACTIVE", "PAUSED"}:
        raise ValueError("status must be ACTIVE or PAUSED")
    return s


class AdsStairway:
    """
    DB-backed Meta Ads execution layer.

    Flow:
    campaign → adset → asset upload → creative → ad
    """

    def __init__(
        self,
        database_url: str,
        encryption_key: str,
        meta_user_id: str,
        client_id: str,
        page_id: str,
        instagram_actor_id: str,
        graph_version: str = "v17.0",
    ) -> None:
        self.graph_version = graph_version
        self.client_id = client_id
        self.page_id = page_id
        self.instagram_actor_id = instagram_actor_id
        self.meta_user_id = meta_user_id

        self.reader = MetaTokenDbReader(database_url, encryption_key)

        self.user_access_token = self._resolve_token(
            self.reader.get_active_user_token(client_id, meta_user_id)
        )
        self.page_access_token = self._resolve_token(
            self.reader.get_active_page_token(client_id, page_id)
        )

        self.campaigns: List[schemas.Campaign] = []
        self.created_campaigns: List[schemas.CreatedCampaign] = []
        self.adsets: List[schemas.AdSet] = []

        # ✅ image_hash must NOT live on AdSet (Pydantic-safe)
        self._image_hash_by_adset_id: dict[str, str] = {}

    # ---------------- INTERNAL ----------------
    @staticmethod
    def _resolve_token(row) -> str:
        if isinstance(row, str):
            return row
        if isinstance(row, dict):
            return str(row["access_token"])
        return str(row.access_token)

    # ---------------- ACCOUNTS ----------------
    def get_ad_accounts(self, campaign_name: str, objective: str):
        url = f"https://graph.facebook.com/{self.graph_version}/me/adaccounts"
        params = {"access_token": self.user_access_token, "fields": "id,name"}

        result = requests.get(url, params=params).json()
        if "error" in result:
            raise Exception(result["error"])

        accounts = []
        for row in result.get("data", []):
            ad_account_id = row["id"] if row["id"].startswith("act_") else f"act_{row['id']}"
            self.campaigns.append(
                schemas.Campaign(
                    ad_account_id=ad_account_id,
                    name=campaign_name,
                    objective=objective,
                )
            )
            accounts.append({"id": ad_account_id, "name": row.get("name")})

        return {"ad_accounts": accounts}

    # ---------------- CAMPAIGN ----------------
   # app/models/ads_stairway.py

    def create_campaign_by_index(self, index: int, status: MetaStatus = "PAUSED"):
        if index >= len(self.campaigns):
            raise Exception("Invalid campaign index")

        campaign = self.campaigns[index]
        url = f"https://graph.facebook.com/{self.graph_version}/{campaign.ad_account_id}/campaigns"

        payload = {
            "name": campaign.name,
            "objective": campaign.objective.strip(),
            "status": normalize_status(status),
            "special_ad_categories": ["NONE"],

            # MUST be an actual boolean -> send via json=
            "is_adset_budget_sharing_enabled": False,

            "access_token": self.user_access_token,
        }

        result = requests.post(url, json=payload).json()
        if "error" in result:
            raise Exception(result["error"])

        created = schemas.CreatedCampaign(
            campaign_id=result["id"],
            ad_account_id=campaign.ad_account_id,
            name=campaign.name,
            objective=campaign.objective,
            page_id=self.page_id,
        )
        self.created_campaigns.append(created)
        return created


    # ---------------- ADSET ----------------
    
    def create_adset(
    self,
    index: int,
    status: MetaStatus = "PAUSED",
    asset_type: AssetType = "video",
    daily_budget: int | None = None,
    title: str | None = None,
    link: str | None = None,
):
        """
        asset_type:
        - "video": includes reels placements
        - "image": avoids reels placements (more compatible)
        daily_budget/title/link are stored in the AdSet model for later creative creation.
        """

        if index >= len(self.created_campaigns):
            raise Exception("Invalid campaign index")

        campaign = self.created_campaigns[index]
        url = f"https://graph.facebook.com/{self.graph_version}/{campaign.ad_account_id}/adsets"

        # Placement differences
        if asset_type == "video":
            ig_positions = ["stream", "story", "reels"]
        else:
            ig_positions = ["stream", "story"]

        final_budget = int(daily_budget) if daily_budget is not None else int(campaign.daily_budget)

        payload = {
            "name": campaign.name,
            "campaign_id": campaign.campaign_id,
            "daily_budget": final_budget,

            "billing_event": "IMPRESSIONS",
            "optimization_goal": campaign.optimization_goal,
            "status": normalize_status(status),

            "promoted_object": {"page_id": campaign.page_id},

            "targeting": {
                "geo_locations": {"countries": ["LB"]},
                "publisher_platforms": ["instagram"],
                "instagram_positions": ig_positions,
                "facebook_positions": [],
            },

            # ✅ prevents "bid amount required" errors
            "bid_strategy": "LOWEST_COST_WITHOUT_CAP",

            "access_token": self.user_access_token,
        }

        result = requests.post(url, json=payload).json()
        if "error" in result:
            raise Exception(result["error"])

        adset = schemas.AdSet(
            adset_id=result["id"],
            campaign_id=campaign.campaign_id,
            ad_account_id=campaign.ad_account_id,
            name=campaign.name,
            daily_budget=final_budget,
            page_id=campaign.page_id,
            status=normalize_status(status),
            link=(link or "youtube.com"),
            title=(title or "Check this out!"),
            asset_type=asset_type,
        )
        self.adsets.append(adset)
        return adset






    # ---------------- VIDEO ----------------
    def upload_ad_video(self, adset_index: int, video_url: str):
        adset = self.adsets[adset_index]

        url = f"https://graph.facebook.com/{self.graph_version}/{self.page_id}/videos"
        payload = {"file_url": video_url, "access_token": self.page_access_token}

        result = requests.post(url, data=payload).json()
        if "error" in result:
            raise Exception(result["error"])

        adset.video_id = result["id"]
        return adset.video_id

    def create_paid_ig_ad(self, adset_index: int, ad_name: str, thumbnail_url: str, status: MetaStatus):
        adset = self.adsets[adset_index]

        url = f"https://graph.facebook.com/{self.graph_version}/{adset.ad_account_id}/adcreatives"
        creative_payload = {
            "name": ad_name,
            "object_story_spec": {
                "page_id": self.page_id,
                "instagram_user_id": self.instagram_actor_id,
                "video_data": {
                    "video_id": adset.video_id,
                    "image_url": thumbnail_url,
                },
            },
            "access_token": self.user_access_token,
        }

        creative = requests.post(url, json=creative_payload).json()
        if "error" in creative:
            raise Exception(creative["error"])

        ad_url = f"https://graph.facebook.com/{self.graph_version}/{adset.ad_account_id}/ads"
        ad_payload = {
            "name": ad_name,
            "adset_id": adset.adset_id,
            "creative": {"creative_id": creative["id"]},
            "status": normalize_status(status),
            "access_token": self.user_access_token,
        }

        ad = requests.post(ad_url, json=ad_payload).json()
        if "error" in ad:
            raise Exception(ad["error"])

        adset.ad_id = ad["id"]
        return {"ad_id": ad["id"], "creative_id": creative["id"]}

    # ---------------- IMAGE ----------------
    def upload_ad_image(self, adset_index: int, image_path: str) -> str:
        adset = self.adsets[adset_index]
        endpoint = f"https://graph.facebook.com/{self.graph_version}/{adset.ad_account_id}/adimages"

        filename = os.path.basename(image_path)
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"

        with open(image_path, "rb") as f:
            files = {"filename": (filename, f, mime)}
            params = {"access_token": self.user_access_token}
            r = requests.post(endpoint, params=params, files=files)

        result = r.json()
        if "error" in result:
            raise Exception(result["error"])

        image_hash = next(iter(result["images"].values()))["hash"]
        self._image_hash_by_adset_id[adset.adset_id] = image_hash
        return image_hash

    def create_paid_ig_image_ad(
        self,
        adset_index: int,
        ad_name: str,
        status: MetaStatus,
        link_url: str,
    ):
        adset = self.adsets[adset_index]
        image_hash = self._image_hash_by_adset_id.get(adset.adset_id)
        if not image_hash:
            raise Exception("Missing image_hash (upload_ad_image first)")

        creative_url = f"https://graph.facebook.com/{self.graph_version}/{adset.ad_account_id}/adcreatives"
        creative_payload = {
            "name": ad_name,
            "object_story_spec": {
                "page_id": self.page_id,
                "instagram_user_id": self.instagram_actor_id,
                "link_data": {
                    "image_hash": image_hash,
                    "link": link_url,
                },
            },
            "access_token": self.user_access_token,
        }

        creative = requests.post(creative_url, json=creative_payload).json()
        if "error" in creative:
            raise Exception(creative["error"])

        ad_url = f"https://graph.facebook.com/{self.graph_version}/{adset.ad_account_id}/ads"
        ad_payload = {
            "name": ad_name,
            "adset_id": adset.adset_id,
            "creative": {"creative_id": creative["id"]},
            "status": normalize_status(status),
            "access_token": self.user_access_token,
        }

        ad = requests.post(ad_url, json=ad_payload).json()
        if "error" in ad:
            raise Exception(ad["error"])

        adset.ad_id = ad["id"]
        return {"ad_id": ad["id"], "creative_id": creative["id"]}


    #         ---------------------CAROUSEL---------------------
    def upload_ad_images(self, adset_index: int, image_paths: list[str]) -> list[str]:
        """
        Upload multiple local images and return their image_hashes (for carousel).
        """
        hashes: list[str] = []
        for p in image_paths:
            h = self.upload_ad_image(adset_index=adset_index, image_path=p)
            hashes.append(h)

        # store list for the adset
        adset = self.adsets[adset_index]
        # keep a separate map for carousel
        if not hasattr(self, "_carousel_hashes_by_adset_id"):
            self._carousel_hashes_by_adset_id: dict[str, list[str]] = {}
        self._carousel_hashes_by_adset_id[adset.adset_id] = hashes
        return hashes


        # -------- CREATE CAROUSEL CREATIVE --------

    def create_ig_mixed_carousel_ad_creative(
        self,
        adset_index: int,
        ad_name: str,
        child_attachments: list[dict],
        link_url: str,
    ) -> str:
        """
        Mixed carousel: images + videos
        Requirements:
        - parent link_data MUST include image_hash (cover) or picture
        - each child attachment should include image_hash (even video cards: thumbnail hash)
        child_attachments items example:
        {"name":"Card 1","link": "...","image_hash":"..."}                      # image card
        {"name":"Card 2","link": "...","video_id":"...","image_hash":"..."}    # video card w/ thumb
        """
        adset = self.adsets[adset_index]

        if not child_attachments or len(child_attachments) < 2:
            raise Exception("Carousel requires at least 2 child attachments")

        # Validate children + pick a cover hash
        cover_hash = None
        for i, att in enumerate(child_attachments, start=1):
            if not isinstance(att, dict):
                raise Exception(f"child_attachments[{i}] must be dict")

            img_hash = (att.get("image_hash") or "").strip()
            has_image = bool(img_hash)
            has_video = bool((att.get("video_id") or "").strip())

            if not has_image:
                raise Exception(
                    f"child_attachments[{i}] missing image_hash. "
                    f"Meta requires image_hash for carousel cards (videos should include a thumbnail image_hash)."
                )

            if not (has_image or has_video):
                raise Exception(f"child_attachments[{i}] must include image_hash and/or video_id")

            if cover_hash is None:
                cover_hash = img_hash

            # Ensure link exists
            if not (att.get("link") or "").strip():
                att["link"] = link_url

        if not cover_hash:
            raise Exception("Could not determine cover image_hash for carousel")

        url = f"https://graph.facebook.com/{self.graph_version}/{adset.ad_account_id}/adcreatives"

        payload = {
            "name": ad_name,
            "object_story_spec": {
                "page_id": self.page_id,
                "instagram_user_id": self.instagram_actor_id,
                "link_data": {
                    # REQUIRED: cover for parent link_data
                    "image_hash": cover_hash,
                    "link": link_url,
                    "name": ad_name,
                    "child_attachments": child_attachments,
                },
            },
            "access_token": self.user_access_token,
        }

        result = requests.post(url, json=payload).json()
        if "error" in result:
            raise Exception(result["error"])
        return result["id"]


    def create_paid_ig_mixed_carousel_ad(
        self,
        adset_index: int,
        ad_name: str,
        child_attachments: list[dict],
        status: MetaStatus = "PAUSED",
        link_url: str | None = None,
    ):
        final_link = link_url or "https://www.instagram.com/"
        creative_id = self.create_ig_mixed_carousel_ad_creative(
            adset_index=adset_index,
            ad_name=ad_name,
            child_attachments=child_attachments,
            link_url=final_link,
        )

        adset = self.adsets[adset_index]
        url = f"https://graph.facebook.com/{self.graph_version}/{adset.ad_account_id}/ads"
        payload = {
            "name": ad_name,
            "adset_id": adset.adset_id,
            "creative": {"creative_id": creative_id},
            "status": normalize_status(status),
            "access_token": self.user_access_token,
        }

        result = requests.post(url, json=payload).json()
        if "error" in result:
            raise Exception(result["error"])

        adset.ad_id = result["id"]
        return {"ad_id": adset.ad_id, "creative_id": creative_id}


    def upload_ad_video_to_account(self, adset_index: int, video_path: str) -> str:
        """
        Upload video to the ad account (advideos) and return video_id.
        This is what you want for carousel child_attachments that include video.
        """
        adset = self.adsets[adset_index]
        ad_account_id = adset.ad_account_id  # act_...

        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video not found: {video_path}")

        endpoint = f"https://graph.facebook.com/{self.graph_version}/{ad_account_id}/advideos"
        filename = os.path.basename(video_path)

        mime = mimetypes.guess_type(filename)[0] or "video/mp4"

        files = {"source": (filename, open(video_path, "rb"), mime)}
        data = {"access_token": self.user_access_token}

        try:
            r = requests.post(endpoint, data=data, files=files, timeout=120)
            result = r.json()
            if "error" in result:
                raise Exception(result["error"])
            video_id = result.get("id")
            if not video_id:
                raise Exception(f"Unexpected response (no id): {result}")
            return video_id
        finally:
            try:
                files["source"][1].close()
            except Exception:
                pass



    def create_ig_mixed_carousel_ad_creative(
        self,
        adset_index: int,
        ad_name: str,
        child_attachments: list[dict],
        link_url: str,
    ) -> str:
        """
        child_attachments items:
        - image card: {"name": "...", "link": "...", "image_hash": "..."}
        - video card: {"name": "...", "link": "...", "video_id": "..."}
        """
        adset = self.adsets[adset_index]
        url = f"https://graph.facebook.com/{self.graph_version}/{adset.ad_account_id}/adcreatives"

        payload = {
            "name": ad_name,
            "object_story_spec": {
                "page_id": self.page_id,
                "instagram_user_id": self.instagram_actor_id,
                "link_data": {
                    "link": link_url,
                    "message": ad_name,  # optional
                    "child_attachments": child_attachments,
                },
            },
            "access_token": self.user_access_token,
        }

        result = requests.post(url, json=payload).json()
        if "error" in result:
            raise Exception(result["error"])
        return result["id"]


    def create_paid_ig_mixed_carousel_ad(
        self,
        adset_index: int,
        ad_name: str,
        child_attachments: list[dict],
        status: MetaStatus = "PAUSED",
        link_url: str | None = None,
    ) -> dict:
        final_link = link_url or "https://www.instagram.com/"
        creative_id = self.create_ig_mixed_carousel_ad_creative(
            adset_index=adset_index,
            ad_name=ad_name,
            child_attachments=child_attachments,
            link_url=final_link,
        )

        adset = self.adsets[adset_index]
        url = f"https://graph.facebook.com/{self.graph_version}/{adset.ad_account_id}/ads"

        payload = {
            "name": ad_name,
            "adset_id": adset.adset_id,
            "creative": {"creative_id": creative_id},
            "status": normalize_status(status),
            "access_token": self.user_access_token,
        }

        result = requests.post(url, json=payload).json()
        if "error" in result:
            raise Exception(result["error"])

        adset.ad_id = result["id"]
        return {"ad_id": adset.ad_id, "creative_id": creative_id}
