# app/models/ads_stairway.py
from __future__ import annotations

import os
import json
import mimetypes
import requests
from typing import List, Literal, Optional, TypedDict

from app.models import schemas
from app.routers.meta_token_db_reader import MetaTokenDbReader

MetaStatus = Literal["ACTIVE", "PAUSED"]
AssetType = Literal["video", "image"]


def normalize_status(status: MetaStatus) -> str:
    s = (status or "").strip().upper()
    if s not in {"ACTIVE", "PAUSED"}:
        raise ValueError("status must be ACTIVE or PAUSED")
    return s


class VideoCarouselCard(TypedDict):
    video_id: str
    image_hash: str  # thumbnail hash


class AdsStairway:
    """
    DB-backed Meta Ads execution layer.

    Flow:
      campaign → adset → asset upload → creative → ad

    Key decisions (important for your carousel mismatch issue):
      - Uses form-encoded payloads for Marketing API (data=)
      - Sends object_story_spec as json string
      - Carousel uses the OLD unified API:
          object_story_spec.link_data.child_attachments
        and DOES NOT send interactive_components_spec
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

        # Keep hashes outside AdSet (Pydantic-safe)
        self._image_hash_by_adset_id: dict[str, str] = {}
        self._carousel_hashes_by_adset_id: dict[str, list[str]] = {}

    # ---------------- INTERNAL ----------------
    @staticmethod
    def _resolve_token(row) -> str:
        if isinstance(row, str):
            return row
        if isinstance(row, dict):
            return str(row["access_token"])
        return str(row.access_token)

    @staticmethod
    def _dbg(tag: str, payload) -> None:
        try:
            print(f"\n[DBG] {tag}:")
            print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        except Exception:
            print(f"\n[DBG] {tag}: {payload!r}")

    @staticmethod
    def _post_form(url: str, payload: dict) -> dict:
        """
        Marketing API is often most reliable with form-encoded payloads.
        Any nested objects must be JSON-serialized manually.
        """
        resp = requests.post(url, data=payload)
        try:
            return resp.json()
        except Exception:
            return {"_raw": resp.text}

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
    def create_campaign_by_index(self, index: int, status: MetaStatus = "PAUSED"):
        if index >= len(self.campaigns):
            raise Exception("Invalid campaign index")

        campaign = self.campaigns[index]
        url = f"https://graph.facebook.com/{self.graph_version}/{campaign.ad_account_id}/campaigns"

        payload = {
            "name": campaign.name,
            "objective": campaign.objective.strip(),
            "status": normalize_status(status),
            "special_ad_categories": json.dumps(["NONE"]),
            "is_adset_budget_sharing_enabled": "false",
            "access_token": self.user_access_token,
        }

        self._dbg("campaign.create.request", {"url": url, "payload": payload})
        result = self._post_form(url, payload)
        self._dbg("campaign.create.response", result)

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
        """
        if index >= len(self.created_campaigns):
            raise Exception("Invalid campaign index")

        campaign = self.created_campaigns[index]
        url = f"https://graph.facebook.com/{self.graph_version}/{campaign.ad_account_id}/adsets"

        ig_positions = ["stream", "story", "reels"] if asset_type == "video" else ["stream", "story"]
        final_budget = int(daily_budget) if daily_budget is not None else int(campaign.daily_budget)

        promoted_object = {"page_id": str(campaign.page_id)}
        targeting = {
            "geo_locations": {"countries": ["LB"]},
            "publisher_platforms": ["instagram"],
            "instagram_positions": ig_positions,
            "facebook_positions": [],
        }

        payload = {
            "name": campaign.name,
            "campaign_id": str(campaign.campaign_id),
            "daily_budget": str(final_budget),
            "billing_event": "IMPRESSIONS",
            "optimization_goal": str(campaign.optimization_goal),
            "status": normalize_status(status),
            "promoted_object": json.dumps(promoted_object),
            "targeting": json.dumps(targeting),
            "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
            "access_token": self.user_access_token,
        }

        self._dbg("adset.create.request", {"url": url, "payload": payload})
        result = self._post_form(url, payload)
        self._dbg("adset.create.response", result)

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

    # ---------------- VIDEO (SINGLE) ----------------
    def upload_ad_video(self, adset_index: int, video_url: str):
        """
        Upload a hosted (public) video URL to the IG page video endpoint.
        """
        adset = self.adsets[adset_index]
        url = f"https://graph.facebook.com/{self.graph_version}/{self.page_id}/videos"
        payload = {"file_url": video_url, "access_token": self.page_access_token}

        self._dbg("upload_ad_video.request", {"url": url, "payload": payload})
        result = requests.post(url, data=payload).json()
        self._dbg("upload_ad_video.response", result)

        if "error" in result:
            raise Exception(result["error"])

        adset.video_id = result["id"]
        return adset.video_id

    def create_paid_ig_ad(self, adset_index: int, ad_name: str, thumbnail_url: str, status: MetaStatus):
        """
        Video ad (single).
        """
        adset = self.adsets[adset_index]
        url = f"https://graph.facebook.com/{self.graph_version}/{adset.ad_account_id}/adcreatives"

        object_story_spec = {
            "page_id": str(self.page_id),
            "instagram_user_id": str(self.instagram_actor_id),
            "video_data": {
                "video_id": str(adset.video_id),
                "image_url": thumbnail_url,
            },
        }

        payload = {
            "name": ad_name,
            "object_story_spec": json.dumps(object_story_spec),
            "access_token": self.user_access_token,
        }

        self._dbg("creative.video.request", {"url": url, "payload": payload, "object_story_spec": object_story_spec})
        creative = self._post_form(url, payload)
        self._dbg("creative.video.response", creative)

        if "error" in creative:
            raise Exception(creative["error"])

        ad_url = f"https://graph.facebook.com/{self.graph_version}/{adset.ad_account_id}/ads"
        ad_payload = {
            "name": ad_name,
            "adset_id": str(adset.adset_id),
            "creative": json.dumps({"creative_id": creative["id"]}),
            "status": normalize_status(status),
            "access_token": self.user_access_token,
        }

        self._dbg("ad.create.video.request", {"url": ad_url, "payload": ad_payload})
        ad = self._post_form(ad_url, ad_payload)
        self._dbg("ad.create.video.response", ad)

        if "error" in ad:
            raise Exception(ad["error"])

        adset.ad_id = ad["id"]
        return {"ad_id": ad["id"], "creative_id": creative["id"]}

    # ---------------- IMAGE (SINGLE) ----------------
    def upload_ad_image(self, adset_index: int, image_path: str) -> str:
        """
        Upload local image to adimages and return image_hash.
        """
        adset = self.adsets[adset_index]
        endpoint = f"https://graph.facebook.com/{self.graph_version}/{adset.ad_account_id}/adimages"

        filename = os.path.basename(image_path)
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"

        self._dbg("upload_ad_image.request", {"endpoint": endpoint, "filename": filename, "mime": mime})

        with open(image_path, "rb") as f:
            files = {"filename": (filename, f, mime)}
            params = {"access_token": self.user_access_token}
            r = requests.post(endpoint, params=params, files=files)

        result = r.json()
        self._dbg("upload_ad_image.response", result)

        if "error" in result:
            raise Exception(result["error"])

        image_hash = next(iter(result["images"].values()))["hash"]
        self._image_hash_by_adset_id[adset.adset_id] = image_hash
        self._dbg("upload_ad_image.image_hash", {"image_hash": image_hash, "adset_id": adset.adset_id})
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

        object_story_spec = {
            "page_id": str(self.page_id),
            "instagram_user_id": str(self.instagram_actor_id),
            "link_data": {
                "image_hash": image_hash,
                "link": link_url,
            },
        }

        payload = {
            "name": ad_name,
            "object_story_spec": json.dumps(object_story_spec),
            "access_token": self.user_access_token,
        }

        self._dbg("creative.image.request", {"url": creative_url, "payload": payload, "object_story_spec": object_story_spec})
        creative = self._post_form(creative_url, payload)
        self._dbg("creative.image.response", creative)

        if "error" in creative:
            raise Exception(creative["error"])

        ad_url = f"https://graph.facebook.com/{self.graph_version}/{adset.ad_account_id}/ads"
        ad_payload = {
            "name": ad_name,
            "adset_id": str(adset.adset_id),
            "creative": json.dumps({"creative_id": creative["id"]}),
            "status": normalize_status(status),
            "access_token": self.user_access_token,
        }

        self._dbg("ad.create.image.request", {"url": ad_url, "payload": ad_payload})
        ad = self._post_form(ad_url, ad_payload)
        self._dbg("ad.create.image.response", ad)

        if "error" in ad:
            raise Exception(ad["error"])

        adset.ad_id = ad["id"]
        return {"ad_id": ad["id"], "creative_id": creative["id"]}

    # =========================
    # CAROUSEL UPLOAD HELPERS
    # =========================
    def upload_ad_images(self, adset_index: int, image_paths: list[str]) -> list[str]:
        """
        Upload multiple local images and return their image_hashes.
        Intended for images-only carousels (or as thumbnails for video cards).
        """
        self._dbg("carousel.upload_images.input", {"adset_index": adset_index, "image_paths": image_paths})

        hashes: list[str] = []
        for i, p in enumerate(image_paths, start=1):
            h = self.upload_ad_image(adset_index=adset_index, image_path=p)
            hashes.append(h)
            self._dbg("carousel.upload_images.progress", {"i": i, "path": p, "hash": h, "hashes_so_far": hashes})

        adset = self.adsets[adset_index]
        self._carousel_hashes_by_adset_id[adset.adset_id] = hashes

        self._dbg("carousel.upload_images.output", {"adset_id": adset.adset_id, "hashes": hashes})
        return hashes

    def upload_ad_video_to_account(self, adset_index: int, video_path: str) -> str:
        """
        Upload local video to the ad account (advideos) and return video_id.
        Useful for carousel video cards if you do not want to host videos publicly.
        """
        adset = self.adsets[adset_index]
        ad_account_id = adset.ad_account_id

        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video not found: {video_path}")

        endpoint = f"https://graph.facebook.com/{self.graph_version}/{ad_account_id}/advideos"
        filename = os.path.basename(video_path)
        mime = mimetypes.guess_type(filename)[0] or "video/mp4"

        self._dbg("upload_ad_video_to_account.request", {"endpoint": endpoint, "filename": filename, "mime": mime})

        f = open(video_path, "rb")
        try:
            files = {"source": (filename, f, mime)}
            data = {"access_token": self.user_access_token}
            r = requests.post(endpoint, data=data, files=files, timeout=120)
            result = r.json()
            self._dbg("upload_ad_video_to_account.response", result)

            if "error" in result:
                raise Exception(result["error"])
            video_id = result.get("id")
            if not video_id:
                raise Exception(f"Unexpected response (no id): {result}")
            return str(video_id)
        finally:
            try:
                f.close()
            except Exception:
                pass

    def upload_ad_videos_to_account(self, adset_index: int, video_paths: list[str]) -> list[str]:
        """
        Upload multiple local videos to /advideos and return their video_ids.
        Intended for videos-only carousels (or mixed carousels).
        """
        self._dbg("carousel.upload_videos.input", {"adset_index": adset_index, "video_paths": video_paths})

        ids: list[str] = []
        for i, p in enumerate(video_paths, start=1):
            vid = self.upload_ad_video_to_account(adset_index=adset_index, video_path=p)
            ids.append(vid)
            self._dbg("carousel.upload_videos.progress", {"i": i, "path": p, "video_id": vid, "ids_so_far": ids})

        self._dbg("carousel.upload_videos.output", {"video_ids": ids})
        return ids

    # =========================
    # CAROUSEL CREATIVE HELPERS
    # =========================
    def _normalize_carousel_attachments(
        self,
        child_attachments: list[dict],
        link_url: str,
    ) -> tuple[str, list[dict]]:
        """
        Validates carousel attachments and returns:
          - cover_image_hash
          - normalized child_attachments

        Rules aligned with Meta behavior:
          - 2..10 cards
          - each card must include image_hash (video cards must include thumb image_hash)
          - video_id optional
          - card link defaults to link_url
        """
        self._dbg("normalize.input", {"link_url": link_url, "child_attachments": child_attachments})

        if not child_attachments or len(child_attachments) < 2:
            raise Exception("Carousel requires at least 2 child attachments")
        if len(child_attachments) > 10:
            raise Exception("Carousel supports at most 10 child attachments")

        cover_hash: Optional[str] = None
        normalized: list[dict] = []

        for i, att in enumerate(child_attachments, start=1):
            if not isinstance(att, dict):
                raise Exception(f"child_attachments[{i}] must be dict")

            img_hash = (att.get("image_hash") or "").strip()
            video_id = (att.get("video_id") or "").strip()

            self._dbg("normalize.card.raw", {"i": i, "att": att, "img_hash": img_hash, "video_id": video_id})

            if not img_hash:
                raise Exception(
                    f"child_attachments[{i}] missing image_hash. "
                    "For carousel link_data, Meta expects image_hash on each card "
                    "(for video cards, use the thumbnail image_hash)."
                )

            if cover_hash is None:
                cover_hash = img_hash

            card = {
                "name": (att.get("name") or f"Card {i}"),
                "link": ((att.get("link") or "").strip() or link_url),
                "image_hash": img_hash,
            }
            if video_id:
                card["video_id"] = video_id

            normalized.append(card)
            self._dbg("normalize.card.normalized", {"i": i, "card": card, "cover_hash_so_far": cover_hash})

        if not cover_hash:
            raise Exception("Could not determine cover image_hash for carousel")

        self._dbg("normalize.output", {"cover_hash": cover_hash, "normalized": normalized})
        return cover_hash, normalized

    # =========================
    # CAROUSEL CREATIVES (OLD UNIFIED)
    # =========================
    
    # --- Add these helper functions inside AdsStairway (near _dbg / _post_form) ---

    @staticmethod
    def _safe_json_loads(s: str):
        try:
            return json.loads(s)
        except Exception:
            return {"_raw": s}

    def _dbg_request_packet(self, tag: str, url: str, payload: dict) -> None:
        """
        Logs EXACTLY what you're sending to requests.post(...).
        - If you use form payload (data=), it prints keys + parsed object_story_spec.
        - If you use json payload (json=), it prints the dict directly.
        """
        pkt = {"url": url}

        # copy without mutating original
        p = dict(payload or {})
        pkt["payload_keys"] = sorted(list(p.keys()))

        # show tokens safely
        if "access_token" in p:
            tok = str(p["access_token"])
            pkt["access_token_preview"] = tok[:8] + "..." + tok[-6:] if len(tok) > 20 else tok

        # decode object_story_spec if it's a JSON string
        if "object_story_spec" in p:
            oss = p["object_story_spec"]
            if isinstance(oss, str):
                pkt["object_story_spec_is_str"] = True
                pkt["object_story_spec_parsed"] = self._safe_json_loads(oss)
            else:
                pkt["object_story_spec_is_str"] = False
                pkt["object_story_spec_value"] = oss

        # decode degrees_of_freedom_spec if present
        if "degrees_of_freedom_spec" in p:
            dof = p["degrees_of_freedom_spec"]
            if isinstance(dof, str):
                pkt["degrees_of_freedom_spec_parsed"] = self._safe_json_loads(dof)
            else:
                pkt["degrees_of_freedom_spec_value"] = dof

        self._dbg(tag, pkt)

    def _dbg_carousel_counts(self, tag: str, object_story_spec: dict) -> None:
        """
        Logs child_attachments count and whether any interactive_components_spec exists anywhere.
        This is specifically to diagnose mismatch errors.
        """
        link_data = (object_story_spec or {}).get("link_data") or {}
        cards = link_data.get("child_attachments") or []
        has_interactive_top = "interactive_components_spec" in (object_story_spec or {})
        has_interactive_link = "interactive_components_spec" in link_data

        self._dbg(tag, {
            "child_attachments_count": len(cards),
            "child_attachments_preview": cards[:2],
            "has_interactive_components_spec_top_level": has_interactive_top,
            "has_interactive_components_spec_in_link_data": has_interactive_link,
            "object_story_spec_keys": sorted(list((object_story_spec or {}).keys())),
            "link_data_keys": sorted(list(link_data.keys())) if isinstance(link_data, dict) else type(link_data).__name__,
        })


    # --- Then, in the WORKING create_ig_carousel_ad_creative (the json= version), add these prints ---

    def create_ig_carousel_ad_creative(
        self,
        adset_index: int,
        ad_name: str,
        image_hashes: list[str],
        link_url: str | None = None,
    ) -> str:
        adset = self.adsets[adset_index]

        if not image_hashes or len(image_hashes) < 2:
            raise Exception("Carousel requires at least 2 image hashes")

        if len(set(image_hashes)) != len(image_hashes):
            print("[WARN] Duplicate image hashes detected in carousel. Consider using different images.")

        final_link = link_url or getattr(adset, "link", None) or "https://www.instagram.com/"
        message = getattr(adset, "title", None) or ad_name

        child_attachments = [{"image_hash": h, "link": final_link} for h in image_hashes]

        url = f"https://graph.facebook.com/{self.graph_version}/{adset.ad_account_id}/adcreatives"

        payload = {
            "name": ad_name,
            "object_story_spec": {
                "page_id": self.page_id,
                "instagram_user_id": self.instagram_actor_id,
                "link_data": {
                    "link": final_link,
                    "message": message,
                    "child_attachments": child_attachments,
                    "multi_share_optimized": False,
                    "multi_share_end_card": False,
                },
            },
            "access_token": self.user_access_token,
        }

        # ---- DEBUG: show structure + counts right before sending ----
        self._dbg("carousel.working.payload.summary", {
            "ad_name": ad_name,
            "final_link": final_link,
            "image_hashes_count": len(image_hashes),
            "image_hashes_preview": image_hashes[:3],
        })
        self._dbg_carousel_counts("carousel.working.object_story_spec.counts", payload["object_story_spec"])
        self._dbg_request_packet("carousel.working.request.packet", url, payload)
        # ------------------------------------------------------------

        resp = requests.post(url, json=payload)
        try:
            result = resp.json()
        except Exception:
            result = {"_raw": resp.text}

        # ---- DEBUG: show HTTP and response ----
        self._dbg("carousel.working.http", {"status_code": resp.status_code})
        self._dbg("carousel.working.response", result)
        # --------------------------------------

        if "error" in result:
            raise Exception(result["error"])
        return result["id"]


    # --- And in create_paid_ig_homogeneous_carousel_ad add a final debug before creating the ad ---

    def create_paid_ig_homogeneous_carousel_ad(
        self,
        adset_index: int,
        ad_name: str,
        image_hashes: list[str],
        status: MetaStatus = "PAUSED",
        link_url: str | None = None,
    ):
        creative_id = self.create_ig_carousel_ad_creative(
            adset_index=adset_index,
            ad_name=ad_name,
            image_hashes=image_hashes,
            link_url=link_url,
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

        # ---- DEBUG: ad create packet ----
        self._dbg("carousel.working.ad_create.payload", {
            "url": url,
            "payload": {
                "name": payload["name"],
                "adset_id": payload["adset_id"],
                "creative": payload["creative"],
                "status": payload["status"],
                "access_token_preview": str(payload["access_token"])[:8] + "...",
            }
        })
        # --------------------------------

        resp = requests.post(url, json=payload)
        try:
            result = resp.json()
        except Exception:
            result = {"_raw": resp.text}

        self._dbg("carousel.working.ad_create.http", {"status_code": resp.status_code})
        self._dbg("carousel.working.ad_create.response", result)

        if "error" in result:
            raise Exception(result["error"])

        adset.ad_id = result["id"]
        return {"ad_id": adset.ad_id, "creative_id": creative_id}


        # =========================
        # COMPAT WRAPPERS (MATCH YOUR PIPELINE)


    def create_paid_ig_mixed_carousel_ad_json(
    self,
    adset_index: int,
    ad_name: str,
    child_attachments: list[dict],
    status: MetaStatus = "PAUSED",
    link_url: Optional[str] = None,
) -> dict:
            adset = self.adsets[adset_index]

            if not child_attachments or len(child_attachments) < 2:
                raise Exception("Mixed carousel requires at least 2 cards")

            final_link = (link_url or getattr(adset, "link", None) or "https://www.instagram.com/").strip()
            message = getattr(adset, "title", None) or ad_name

            # -------- normalize + validate cards --------
            cards: list[dict] = []
            for i, att in enumerate(child_attachments, start=1):
                if not isinstance(att, dict):
                    raise Exception(f"child_attachments[{i}] must be dict")

                typ = (att.get("type") or "").strip().lower()
                link = (att.get("link") or "").strip() or final_link

                img_hash = (att.get("image_hash") or "").strip()
                vid = (att.get("video_id") or "").strip()

                if typ not in {"image", "video"}:
                    # allow "implicit typing" for convenience
                    typ = "video" if vid else "image"

                if typ == "image":
                    if not img_hash:
                        raise Exception(f"child_attachments[{i}] image card missing image_hash")
                    card = {"link": link, "image_hash": img_hash}

                else:  # video
                    if not vid:
                        raise Exception(f"child_attachments[{i}] video card missing video_id")
                    if not img_hash:
                        raise Exception(
                            f"child_attachments[{i}] video card missing thumbnail image_hash "
                            "(upload thumbnail via upload_ad_image and pass its hash)"
                        )
                    # IMPORTANT: keep thumb image_hash even for video cards
                    card = {"link": link, "video_id": vid, "image_hash": img_hash}

                cards.append(card)

            # Debug: show what we will send
            self._dbg("carousel.mixed.json.cards", {
                "count": len(cards),
                "preview": cards[:3],
            })

            # -------- create creative (JSON) --------
            creative_url = f"https://graph.facebook.com/{self.graph_version}/{adset.ad_account_id}/adcreatives"

            object_story_spec = {
                "page_id": str(self.page_id),
                "instagram_user_id": str(self.instagram_actor_id),
                "link_data": {
                    "link": final_link,
                    "message": message,
                    "child_attachments": cards,
                    "multi_share_optimized": False,
                    "multi_share_end_card": False,
                },
            }

            creative_payload = {
                "name": ad_name,
                "object_story_spec": object_story_spec,
                "access_token": self.user_access_token,
            }

            self._dbg("carousel.mixed.json.creative.request", {
                "url": creative_url,
                "payload_keys": list(creative_payload.keys()),
                "object_story_spec": object_story_spec,
            })

            creative_resp = requests.post(creative_url, json=creative_payload)
            self._dbg("carousel.mixed.json.creative.http", {"status_code": creative_resp.status_code})
            creative = creative_resp.json()
            self._dbg("carousel.mixed.json.creative.response", creative)

            if "error" in creative:
                raise Exception(creative["error"])

            creative_id = str(creative["id"])

            # -------- create ad (JSON) --------
            ad_url = f"https://graph.facebook.com/{self.graph_version}/{adset.ad_account_id}/ads"
            ad_payload = {
                "name": ad_name,
                "adset_id": str(adset.adset_id),
                "creative": {"creative_id": creative_id},
                "status": normalize_status(status),
                "access_token": self.user_access_token,
            }

            self._dbg("carousel.mixed.json.ad.request", {"url": ad_url, "payload": {**ad_payload, "access_token": "REDACTED"}})
            ad_resp = requests.post(ad_url, json=ad_payload)
            self._dbg("carousel.mixed.json.ad.http", {"status_code": ad_resp.status_code})
            ad = ad_resp.json()
            self._dbg("carousel.mixed.json.ad.response", ad)

            if "error" in ad:
                raise Exception(ad["error"])

            adset.ad_id = str(ad["id"])
            return {"ad_id": adset.ad_id, "creative_id": creative_id}
    
    def create_paid_ig_mixed_carousel_ad(
        self,
        adset_index: int,
        ad_name: str,
        child_attachments: list[dict],
        status: MetaStatus = "PAUSED",
        link_url: str | None = None,
    ) -> dict:
        """
        Mixed carousel wrapper (uses unified old child_attachments API).
        """
        return self.create_paid_ig_carousel_ad(
            adset_index=adset_index,
            ad_name=ad_name,
            child_attachments=child_attachments,
            status=status,
            link_url=link_url,
        )
