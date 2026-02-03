# app/models/fb_ads_stairway.py
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests

from app.routers.DB_helpers.meta_token_db_reader import MetaTokenDbReader


# -----------------------------
# Small refs stored internally
# -----------------------------
@dataclass(frozen=True)
class _CampaignRef:
    ad_account_id: str   # MUST be "act_123..."
    campaign_id: str


@dataclass(frozen=True)
class _AdSetRef:
    ad_account_id: str   # MUST be "act_123..."
    campaign_id: str
    adset_id: str


def normalize_act(ad_account_id: str) -> str:
    """
    Ensures the ad account id is exactly "act_<digits>" (no double prefix).
    """
    s = (ad_account_id or "").strip()
    if not s:
        raise ValueError("Empty ad_account_id")

    # de-dup
    while s.startswith("act_act_"):
        s = s.replace("act_act_", "act_", 1)

    if not s.startswith("act_"):
        s = "act_" + s

    return s


class FbAdsStairway:
    """
    Tailored to main_fb_ad_pipeline.py.

    Uses:
      - user token for ad account operations
      - page_id for object_story_spec.page_id
    """

    def __init__(
        self,
        *,
        database_url: str,
        encryption_key: str,
        meta_user_id: str,
        client_id: str,
        page_id: str,
        graph_version: str = "v17.0",
        timeout_s: int = 60,
    ) -> None:
        self.database_url = database_url
        self.encryption_key = encryption_key
        self.meta_user_id = str(meta_user_id)
        self.client_id = str(client_id)
        self.page_id = str(page_id)
        self.graph_version = str(graph_version)
        self.timeout_s = int(timeout_s)

        self._reader = MetaTokenDbReader(database_url=self.database_url, fernet_key=self.encryption_key)

        self.ad_accounts: List[Dict[str, Any]] = []
        self._picked_ad_account_id: Optional[str] = None  # normalized "act_..."

        self._campaign_by_index: Dict[int, _CampaignRef] = {}
        self._adset_by_index: Dict[int, _AdSetRef] = {}

        self.campaign_name: str = ""
        self.objective: str = ""

    # -----------------------------
    # Tokens
    # -----------------------------
    def _user_access_token(self) -> str:
        tok = self._reader.get_active_user_token(self.client_id, self.meta_user_id)
        return tok.access_token

    def _page_access_token(self) -> str:
        tok = self._reader.get_active_page_token(self.client_id, self.page_id)
        return tok.access_token

    # -----------------------------
    # HTTP
    # -----------------------------
    def _base_url(self) -> str:
        return f"https://graph.facebook.com/{self.graph_version}"

    def _req(
        self,
        method: str,
        path: str,
        *,
        token_kind: str = "user",  # "user" or "page"
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        files: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        url = f"{self._base_url()}{path}"
        p = dict(params or {})

        if token_kind == "page":
            p["access_token"] = self._page_access_token()
        else:
            p["access_token"] = self._user_access_token()

        resp = requests.request(
            method=method.upper(),
            url=url,
            params=p,
            data=data,
            files=files,
            timeout=self.timeout_s,
        )

        try:
            payload = resp.json()
        except Exception:
            resp.raise_for_status()
            return {"raw": resp.text}

        if resp.status_code >= 400 or "error" in payload:
            raise RuntimeError(payload)
        return payload

    # -----------------------------
    # STEP 1
    # -----------------------------
    def get_ad_accounts(self, *, campaign_name: str, objective: str) -> Dict[str, Any]:
        self.campaign_name = str(campaign_name)
        self.objective = str(objective)

        me_adaccounts = self._req(
            "GET",
            "/me/adaccounts",
            token_kind="user",
            params={"fields": "id,name,account_status,currency"},
        )

        self.ad_accounts = me_adaccounts.get("data", []) or []
        if not self.ad_accounts:
            raise RuntimeError("No ad accounts returned from /me/adaccounts for this user token.")

        # Normalize once and store normalized
        raw_id = str(self.ad_accounts[0]["id"])
        self._picked_ad_account_id = normalize_act(raw_id)

        return {
            "picked_ad_account_id": self._picked_ad_account_id,
            "ad_accounts": self.ad_accounts,
            "campaign_name": self.campaign_name,
            "objective": self.objective,
        }

    # -----------------------------
    # STEP 2
    # -----------------------------
    def create_campaign_by_index(self, index: int, *, status: str = "PAUSED") -> Dict[str, Any]:
        if not self._picked_ad_account_id:
            raise RuntimeError("Call get_ad_accounts() before create_campaign_by_index().")

        act = normalize_act(self._picked_ad_account_id)

        payload = self._req(
            "POST",
            f"/{act}/campaigns",
            token_kind="user",
            data={
                "name": self.campaign_name or "Automated FB Campaign",
                "objective": self.objective or "OUTCOME_AWARENESS",
                "status": status,
                # Required; safest is ["NONE"]
                "special_ad_categories": json.dumps(["NONE"]),
                # If you are NOT using CBO, Meta requires explicit boolean.
                # String is safest for form-encoded requests.
                "is_adset_budget_sharing_enabled": "false",
            },
        )

        campaign_id = payload.get("id")
        if not campaign_id:
            raise RuntimeError({"message": "Campaign creation did not return id", "payload": payload})

        self._campaign_by_index[int(index)] = _CampaignRef(
            ad_account_id=act,
            campaign_id=str(campaign_id),
        )
        return payload

    # -----------------------------
    # STEP 3
    # -----------------------------
    class _MutableAdSet:
        """
        What your pipeline mutates:
          adset.daily_budget, adset.title, adset.link
        """
        def __init__(self) -> None:
            self.daily_budget: int = 0
            self.title: str = ""
            self.link: str = ""

        def __repr__(self) -> str:
            return f"_MutableAdSet(daily_budget={self.daily_budget}, title={self.title!r}, link={self.link!r})"

    def create_adset(
        self,
        index: int,
        *,
        status: str = "PAUSED",
        asset_type: str = "image",
        platform_mode: str = "facebook",
    ) -> Any:
        ref = self._campaign_by_index.get(int(index))
        if not ref:
            raise RuntimeError("No campaign stored for this index. Call create_campaign_by_index() first.")

        act = normalize_act(ref.ad_account_id)

        # Facebook placements:
        # Valid facebook_positions include: feed, marketplace, video_feeds, story, search, right_hand_column, instream_video, etc.
        # IMPORTANT: "reels" is NOT valid in facebook_positions (that was your error).
        facebook_positions = ["feed", "story"]

        targeting = {
            "geo_locations": {"countries": ["LB"]},
            "publisher_platforms": ["facebook"],
            "facebook_positions": facebook_positions,
        }

        payload = self._req(
            "POST",
            f"/{act}/adsets",
            token_kind="user",
            data={
                "name": f"{self.campaign_name} | FB AdSet {index}",
                "campaign_id": ref.campaign_id,
                "daily_budget": "1000",  # pipeline later mutates local object; this is initial safe default
                "billing_event": "IMPRESSIONS",
                "optimization_goal": "REACH",
                "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
                "status": status,
                "targeting": json.dumps(targeting),
            },
        )

        adset_id = payload.get("id")
        if not adset_id:
            raise RuntimeError({"message": "Ad set creation did not return id", "payload": payload})

        self._adset_by_index[int(index)] = _AdSetRef(
            ad_account_id=act,
            campaign_id=ref.campaign_id,
            adset_id=str(adset_id),
        )

        # Pipeline expects a mutable object to edit (budget/title/link)
        return self._MutableAdSet()

    # -----------------------------
    # Uploads
    # -----------------------------
    def upload_ad_image(self, *, adset_index: int, image_path: str) -> str:
        ref = self._adset_by_index.get(int(adset_index))
        if not ref:
            raise RuntimeError("No ad set stored for this index.")
        act = normalize_act(ref.ad_account_id)

        with open(image_path, "rb") as f:
            payload = self._req(
                "POST",
                f"/{act}/adimages",
                token_kind="user",
                files={"filename": f},
            )

        images = payload.get("images") or {}
        if not images:
            raise RuntimeError({"message": "No images returned", "payload": payload})

        first_key = next(iter(images.keys()))
        image_hash = images[first_key].get("hash")
        if not image_hash:
            raise RuntimeError({"message": "No image hash returned", "payload": payload})

        return str(image_hash)

    def upload_ad_images(self, *, adset_index: int, image_paths: List[str]) -> List[str]:
        return [self.upload_ad_image(adset_index=adset_index, image_path=p) for p in image_paths]

    def upload_ad_video(self, *, adset_index: int, video_url: str) -> str:
        """
        Hosted URL upload to advideos.
        video_url must be publicly accessible (your Spaces URL).
        """
        ref = self._adset_by_index.get(int(adset_index))
        if not ref:
            raise RuntimeError("No ad set stored for this index.")
        act = normalize_act(ref.ad_account_id)

        payload = self._req(
            "POST",
            f"/{act}/advideos",
            token_kind="user",
            data={"file_url": video_url},
        )

        vid = payload.get("id")
        if not vid:
            raise RuntimeError({"message": "No video id returned", "payload": payload})
        return str(vid)

    # -----------------------------
    # Creatives + Ads
    # -----------------------------
    def _create_adcreative(self, *, adset_index: int, name: str, object_story_spec: Dict[str, Any]) -> str:
        ref = self._adset_by_index.get(int(adset_index))
        if not ref:
            raise RuntimeError("No ad set stored for this index.")
        act = normalize_act(ref.ad_account_id)

        payload = self._req(
            "POST",
            f"/{act}/adcreatives",
            token_kind="user",
            data={"name": name, "object_story_spec": json.dumps(object_story_spec)},
        )

        creative_id = payload.get("id")
        if not creative_id:
            raise RuntimeError({"message": "No creative id returned", "payload": payload})
        return str(creative_id)

    def _create_ad(self, *, adset_index: int, name: str, creative_id: str, status: str) -> Dict[str, Any]:
        ref = self._adset_by_index.get(int(adset_index))
        if not ref:
            raise RuntimeError("No ad set stored for this index.")
        act = normalize_act(ref.ad_account_id)

        payload = self._req(
            "POST",
            f"/{act}/ads",
            token_kind="user",
            data={
                "name": name,
                "adset_id": ref.adset_id,
                "status": status,
                "creative": json.dumps({"creative_id": creative_id}),
            },
        )
        if not payload.get("id"):
            raise RuntimeError({"message": "Ad creation did not return id", "payload": payload})
        return payload

    # -----------------------------
    # Pipeline-facing creatives
    # -----------------------------
    def create_paid_fb_image_ad(
        self,
        *,
        adset_index: int,
        ad_name: str,
        primary_text: str,
        link_url: str,
        image_hash: str,
        status: str = "ACTIVE",
        cta_type: str = "LEARN_MORE",
    ) -> Dict[str, Any]:
        object_story_spec = {
            "page_id": self.page_id,
            "link_data": {
                "message": primary_text,
                "link": link_url,
                "image_hash": image_hash,
                "call_to_action": {"type": cta_type, "value": {"link": link_url}},
                "name": ad_name,
            },
        }

        creative_id = self._create_adcreative(
            adset_index=adset_index,
            name=f"{ad_name} | fb_image_creative",
            object_story_spec=object_story_spec,
        )
        return self._create_ad(adset_index=adset_index, name=ad_name, creative_id=creative_id, status=status)
    def create_paid_fb_video_ad(
    self,
    *,
    adset_index: int,
    ad_name: str,
    primary_text: str,
    link_url: str,
    video_id: str,
    thumbnail_url: str = "",
    status: str = "ACTIVE",
    cta_type: str = "LEARN_MORE",
) -> Dict[str, Any]:
        video_data: Dict[str, Any] = {
            "video_id": video_id,
            "message": primary_text,
            "call_to_action": {"type": cta_type, "value": {"link": link_url}},
            "title": ad_name,
        }

        # IMPORTANT: FB requires a thumbnail: image_url or image_hash
        if thumbnail_url:
            video_data["image_url"] = thumbnail_url  # matches Meta docs pattern

        object_story_spec = {"page_id": self.page_id, "video_data": video_data}

        creative_id = self._create_adcreative(
            adset_index=adset_index,
            name=f"{ad_name} | fb_video_creative",
            object_story_spec=object_story_spec,
        )
        return self._create_ad(adset_index=adset_index, name=ad_name, creative_id=creative_id, status=status)

        
    def create_paid_fb_homogeneous_carousel_ad(
        self,
        *,
        adset_index: int,
        ad_name: str,
        primary_text: str,
        link_url: str,
        child_attachments: List[Dict[str, Any]],
        status: str = "ACTIVE",
        cta_type: str = "LEARN_MORE",
    ) -> Dict[str, Any]:
        cleaned: List[Dict[str, Any]] = []
        for i, c in enumerate(child_attachments, start=1):
            item = dict(c)
            item.setdefault("name", f"Card {i}")
            item.setdefault("link", link_url)
            if not item.get("image_hash"):
                raise ValueError(f"Carousel child missing image_hash: {item}")
            cleaned.append(item)

        object_story_spec = {
            "page_id": self.page_id,
            "link_data": {
                "message": primary_text,
                "link": link_url,
                "child_attachments": cleaned,
                "call_to_action": {"type": cta_type, "value": {"link": link_url}},
                "multi_share_optimized": True,
                "name": ad_name,
            },
        }

        creative_id = self._create_adcreative(
            adset_index=adset_index,
            name=f"{ad_name} | fb_carousel_creative",
            object_story_spec=object_story_spec,
        )
        return self._create_ad(adset_index=adset_index, name=ad_name, creative_id=creative_id, status=status)
# inside FbAdsStairway class

    def create_paid_fb_mixed_carousel_ad(
        self,
        *,
        adset_index: int,
        ad_name: str,
        primary_text: str,
        link_url: str,
        child_attachments: List[Dict[str, Any]],
        status: str = "ACTIVE",
        cta_type: str = "LEARN_MORE",
    ) -> Dict[str, Any]:
        """
        Mixed carousel = allow image cards and video cards.
        Each child attachment should be either:
        - {"name", "link", "image_hash"}
        - {"name", "link", "video_id", "image_hash"}  # video card + thumbnail hash
        """

        cleaned: List[Dict[str, Any]] = []
        for i, c in enumerate(child_attachments, start=1):
            item = dict(c)
            item.setdefault("name", f"Card {i}")
            item.setdefault("link", link_url)

            # must have thumbnail for any video card
            if item.get("video_id") and not item.get("image_hash"):
                raise ValueError(f"Video carousel child missing image_hash thumbnail: {item}")

            # must have either image_hash or video_id (video requires thumb hash above)
            if not item.get("image_hash") and not item.get("video_id"):
                raise ValueError(f"Carousel child missing both image_hash and video_id: {item}")

            cleaned.append(item)

        object_story_spec = {
            "page_id": self.page_id,
            "link_data": {
                "message": primary_text,
                "link": link_url,
                "child_attachments": cleaned,
                "call_to_action": {"type": cta_type, "value": {"link": link_url}},
                "multi_share_optimized": True,
                "name": ad_name,
            },
        }

        creative_id = self._create_adcreative(
            adset_index=adset_index,
            name=f"{ad_name} | fb_mixed_carousel_creative",
            object_story_spec=object_story_spec,
        )
        return self._create_ad(adset_index=adset_index, name=ad_name, creative_id=creative_id, status=status)
