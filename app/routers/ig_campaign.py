from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Form
import requests
import os
from typing import List
from app.models.schemas import Campaign, CreatedCampaign, AdSet, AdSetCreate, AdUploadCreate
from app.models.schemas import PaidAdRequest

from dotenv import load_dotenv
import time
import shutil
import uuid
from app.models.schemas import InstagramUploadRequest, InstagramPublishRequest

load_dotenv()
router = APIRouter()
GRAPH_API_VERSION = "v17.0"
ACCESS_TOKEN = os.getenv("META_USER_LONG_LIVED_ACCESS_TOKEN_0")
INSTAGRAM_USER_ID = os.getenv("IG_USER_ID_0")
INSTAGRAM_ACTOR_ID = os.getenv("INSTAGRAM_BUSINESS_ACCOUNT_ID")
PAGE_ID = os.getenv("PAGE_ID")
PAGE_ACCESS_TOKEN=os.getenv("PAGE_ACCESS_TOKEN")
campaigns: List[Campaign] = []
created_campaigns: List[CreatedCampaign] = []
adsets: List[AdSet] = []

# ------------------- GET AD ACCOUNTS -------------------
@router.get("/ping")
def ping():
    return {"message": "pong"}

@router.get("/ad-accounts")
def get_ad_accounts(campaign_name: str, objective: str):
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/me/adaccounts"
    params = {"access_token": ACCESS_TOKEN, "fields": "id,name,account_status"}
    response = requests.get(url, params=params)
    data = response.json()

    if "error" in data:
        raise HTTPException(status_code=400, detail=data["error"])

    ad_accounts = []
    for a in data.get("data", []):
        ad_account_id = a["id"] if a["id"].startswith("act_") else f"act_{a['id']}"
        ad_accounts.append({"id": ad_account_id, "name": a.get("name"), "status": a.get("account_status")})

        # Store campaigns for later creation
        campaigns.append(Campaign(ad_account_id=ad_account_id, name=campaign_name, objective=objective.strip()))

    return {"ad_accounts": ad_accounts, "generated_campaigns": campaigns}

# ------------------- CREATE CAMPAIGN -------------------
@router.post("/campaigns")
def create_campaign_by_index(index: int = Query(..., ge=0)):
    if not campaigns:
        raise HTTPException(status_code=400, detail="No campaigns available. Call GET /ad-accounts first.")
    if index >= len(campaigns): 
        raise HTTPException(status_code=400, detail=f"Index out of range. Max index is {len(campaigns) - 1}")

    campaign = campaigns[index]
    name = campaign.name
    objective = campaign.objective
    ad_account_id = campaign.ad_account_id
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{ad_account_id}/campaigns"
    
    data = {
        "name": name,
        "objective": objective,
        "status": "PAUSED",
        "special_ad_categories": ["NONE"],
        "is_adset_budget_sharing_enabled": False,
        "access_token": ACCESS_TOKEN
    }

    result = requests.post(url, data=data).json()
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    created_campaign = CreatedCampaign(
        ad_account_id=campaign.ad_account_id,
        name=campaign.name,
        objective=campaign.objective,
        campaign_id=result["id"],
        page_id =os.getenv("PAGE_ID")
    )
    created_campaigns.append(created_campaign)

    return {"message": "Campaign created successfully", "used_index": index, "created_campaign": created_campaign.dict(), "meta_response": result}

# ------------------- CREATE ADSET -------------------

@router.post("/adsets")
def create_adset(index: int = Query(...,ge=0)):
    try:
        created_campaign = created_campaigns[index]
        name = created_campaign.name
        campaign_id = created_campaign.campaign_id
        daily_budget = created_campaign.daily_budget
        optimization_goal = created_campaign.optimization_goal
        page_id = created_campaign.page_id
        ad_account_id = created_campaign.ad_account_id



        url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{ad_account_id}/adsets"
        data = {
            "name": name,
            "campaign_id": campaign_id,
            "daily_budget": daily_budget,
            "billing_event": "IMPRESSIONS",
            "optimization_goal": optimization_goal,
            "status": "PAUSED",
            "promoted_object": {"page_id": page_id},
            "targeting": {
                "geo_locations": {"countries": ["LB"]},
                "publisher_platforms": ["instagram"],
                "instagram_positions": ["stream", "story", "reels"],
                "facebook_positions": []
            },
            "access_token": ACCESS_TOKEN,
            "bid_amount": 100
        }

        result = requests.post(url, json=data).json()
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])

        new_adset = AdSet(adset_id=result["id"], campaign_id=campaign_id, ad_account_id=ad_account_id, name=name, daily_budget=daily_budget,page_id=page_id)
        adsets.append(new_adset)
        return {"message": "Ad set created successfully", "adset": new_adset.dict(), "meta_response": result}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ------------------- UPLOAD VIDEO & CREATE AD -------------------
MEDIA_DIR = "media"

@router.post("/upload-video-ngrok/{adset_index}")
def upload_video_ngrok(adset_index: int, video_file: UploadFile = File(...)):
    if adset_index >= len(adsets):
        raise HTTPException(status_code=404, detail="AdSet index out of range")

    adset = adsets[adset_index]

    # save file
    filename = f"{uuid.uuid4()}_{video_file.filename}"
    path = os.path.join(MEDIA_DIR, filename)
    with open(path, "wb") as buffer:
        shutil.copyfileobj(video_file.file, buffer)

    # construct public URL using ngrok
    ngrok_url = os.getenv("NGROK_URL")  # e.g., https://flawy-toni-tritheistical.ngrok-free.dev
    public_video_url = f"{ngrok_url}/media/{filename}"

    # update adset object
    adset.video_url = public_video_url
    

    return {
        "message": "Video uploaded successfully",
        "video_url": public_video_url,
        "adset_index": adset_index
    }














@router.post("/upload-video-fb")
def upload_video(video_file: UploadFile = File(...)):
    page_id = os.getenv("PAGE_ID")  # or get from adset if you want
    files = {"file": (video_file.filename, video_file.file, video_file.content_type)}
    data = {"access_token": PAGE_ACCESS_TOKEN}

    upload_url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{page_id}/videos"
    upload_result = requests.post(upload_url, files=files, data=data).json()

    if "error" in upload_result:
        raise HTTPException(status_code=400, detail=upload_result["error"])

    video_id = upload_result["id"]
    return {"message": "Video uploaded successfully", "video_id": video_id}




@router.post("/upload-video-instagram/{adset_index}")
def upload_video_instagram(adset_index: int, video_url: str | None = None):
    """
    Upload a video to Instagram as a REEL.
    Uses the AdSet object's video_url if none is provided.
    """
    # HOW TO CALL IN POSTMAN : http://127.0.0.1:8000/ig/upload-video-instagram/0?video_url=https://flawy-toni-tritheistical.ngrok-free.dev/media/c8e20e96-9331-45dd-943e-60c9997f9891_istockphoto-2097298327-640_adpp_is.mp4

    if adset_index >= len(adsets):
        raise HTTPException(status_code=404, detail="AdSet index out of range")


    adset = adsets[adset_index]

    # Determine which video URL to use
    if video_url:
      public_video_url = video_url
    elif adset.video_url:
      public_video_url = adset.video_url
    else:
     raise HTTPException(status_code=400, detail="No video URL provided or found in AdSet")

    # Update the AdSet object
    adset.video_url = public_video_url

    # Create Instagram media container
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{INSTAGRAM_USER_ID}/media"
    data = {
        "media_type": "REELS",  # required by Instagram API
        "video_url": adset.video_url,
        "caption": adset.title,  # use adset title
        "access_token": PAGE_ACCESS_TOKEN
    }

    response = requests.post(url, data=data).json()
    if "error" in response:
        raise HTTPException(status_code=400, detail=response["error"])

    adset.creation_id = response["id"]
    
    return {
        "message": "Instagram container created",
        "creation_id": adset.creation_id,
        "video_url": adset.video_url,
        "adset_index": adset_index
    }
    
MAX_RETRIES = 20
RETRY_DELAY = 5  # seconds between status checks

@router.post("/publish-video-instagram/{adset_index}")
def publish_video_instagram(adset_index: int):
    """
    Publishes an Instagram video post for the given adset.
    Will retry until the video is ready to be published.
    """
    if adset_index >= len(adsets):
        raise HTTPException(status_code=404, detail="AdSet index out of range")

    adset = adsets[adset_index]

    if not adset.creation_id:
        raise HTTPException(status_code=400, detail="Creation ID not set on AdSet")

    # Poll the media status before publishing
    status_url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{adset.creation_id}?fields=status_code&access_token={PAGE_ACCESS_TOKEN}"

    for attempt in range(MAX_RETRIES):
        status_resp = requests.get(status_url).json()
        if "error" in status_resp:
            raise HTTPException(status_code=400, detail=status_resp["error"])

        status = status_resp.get("status_code")
        if status == "FINISHED":
            # Media is ready, publish it
            publish_url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{INSTAGRAM_USER_ID}/media_publish"
            data = {
                "creation_id": adset.creation_id,
                "access_token": PAGE_ACCESS_TOKEN
            }
            publish_resp = requests.post(publish_url, data=data).json()
            if "error" in publish_resp:
                raise HTTPException(status_code=400, detail=publish_resp["error"])

            adset.instagram_post_id = publish_resp["id"]
            return {
                "message": "Instagram video published",
                "instagram_post_id": adset.instagram_post_id,
                "adset_index": adset_index
            }

        elif status == "ERROR":
            raise HTTPException(status_code=400, detail="Video failed to process")

        # Wait before next status check
        time.sleep(RETRY_DELAY)

    raise HTTPException(status_code=400, detail="Video not ready after multiple attempts")

# ------------------- UPLOAD VIDEO TO FB PAGE (Paid Ads) -------------------
@router.post("/upload-video/{adset_index}")
def upload_ad_video(adset_index: int, video_file: UploadFile = File(...)):
    if adset_index >= len(adsets):
        raise HTTPException(status_code=404, detail="AdSet index out of range")
    
    adset = adsets[adset_index]

    try:
        files = {"file": (video_file.filename, video_file.file, video_file.content_type)}
        data = {"access_token": PAGE_ACCESS_TOKEN}

        upload_url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{PAGE_ID}/videos"
        upload_result = requests.post(upload_url, files=files, data=data).json()
        print("FB upload response:", upload_result)

        if "error" in upload_result:
            raise HTTPException(status_code=400, detail=upload_result["error"])

        adset.video_id = upload_result.get("id")
        if not adset.video_id:
            raise HTTPException(status_code=500, detail="Video ID not returned by Facebook")

        return {"message": "Video uploaded successfully", "video_id": adset.video_id, "adset_index": adset_index}

    except Exception as e:
        print("Upload video error:", str(e))
        raise HTTPException(status_code=500, detail=str(e))

# ------------------- CREATE IG AD CREATIVE -------------------
def create_ig_ad_creative(adset: AdSet, ad_name: str, thumbnail_url: str):
    if not adset.video_id:
        raise HTTPException(status_code=400, detail="Video ID not set for this AdSet")

    act_ad_account_id = adset.ad_account_id if adset.ad_account_id.startswith("act_") else f"act_{adset.ad_account_id}"
    creative_url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{act_ad_account_id}/adcreatives"

    payload = {
        "name": ad_name,
        "object_story_spec": {
            "page_id": PAGE_ID,
            "instagram_user_id": INSTAGRAM_ACTOR_ID,
            "video_data": {
                "video_id": adset.video_id,
                "image_url": thumbnail_url,
                "call_to_action": {
                    "type": "LEARN_MORE",
                    "value": {"link": adset.link or "https://youtube.com"}
                }
            }
        },
        "access_token": ACCESS_TOKEN
    }

    resp = requests.post(creative_url, json=payload).json()
    if "error" in resp:
        print("[ERROR] Creative creation failed:", resp["error"])
        raise HTTPException(status_code=400, detail=resp["error"])
    return resp["id"]


@router.post("/ads/create/{adset_index}")
def create_paid_ig_ad(adset_index: int, body: PaidAdRequest):
    if adset_index >= len(adsets):
        raise HTTPException(status_code=404, detail="AdSet index out of range")

    adset = adsets[adset_index]

    # Debug: check for missing fields
    for field in ["adset_id", "ad_account_id", "video_id"]:
        if getattr(adset, field) in (None, ...):
            raise HTTPException(status_code=400, detail=f"AdSet field '{field}' is not set properly")

    ad_name = body.ad_name or adset.title
    thumbnail_url = body.thumbnail_url

    # Create creative
    creative_id = create_ig_ad_creative(adset, ad_name, thumbnail_url)

    # Create the actual ad
    act_ad_account_id = adset.ad_account_id if adset.ad_account_id.startswith("act_") else f"act_{adset.ad_account_id}"
    ad_url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{act_ad_account_id}/ads"

    ad_payload = {
        "name": ad_name,
        "adset_id": adset.adset_id,
        "creative": {"creative_id": creative_id},
        "status": "PAUSED",
        "access_token": ACCESS_TOKEN
    }

    ad_resp = requests.post(ad_url, json=ad_payload).json()
    if "error" in ad_resp:
        print("[ERROR] Ad creation failed:", ad_resp["error"])
        raise HTTPException(status_code=400, detail=ad_resp["error"])

    # Save the ad ID in memory
    adset.ad_id = ad_resp["id"]

    # Safe return using .dict() to avoid ellipsis issues
    return {
        "message": "Paid Instagram ad created successfully",
        "ad_id": adset.ad_id,
        "creative_id": creative_id,
        "adset_index": adset_index,
        "adset_snapshot": adset.dict()  # Safe serialization
    }




























































AD_ACCOUNT_ID = os.getenv("AD_ACCOUNT_ID")
def create_ig_reel_ad_creative(
    act_ad_account_id: str,
    ad_name: str,
    instagram_actor_id: str,
    page_id: str,
    video_id: str,
    link: str,
):
    creative_url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{act_ad_account_id}/adcreatives"

    creative_payload = {
        "name": ad_name,
        "object_story_spec": {
            "page_id": page_id,  # ✅ REQUIRED
            "instagram_actor_id": instagram_actor_id,
            "video_data": {
                "video_id": video_id,  # TEMP (creation_id for now)
                "call_to_action": {
                    "type": "LEARN_MORE",
                    "value": {
                        "link": link
                    }
                }
            }
        },
        "access_token": ACCESS_TOKEN  # USER token
    }

    print("[DEBUG] Creative payload:", creative_payload)

    resp = requests.post(creative_url, json=creative_payload).json()

    if "error" in resp:
        raise HTTPException(status_code=400, detail=resp["error"])

    return resp["id"]





@router.post("/create-ig-post-ad/{adset_index}")
def create_ig_post_ad(adset_index: int, ad_name: str | None = None):

    if adset_index >= len(adsets):
        raise HTTPException(status_code=404, detail="AdSet index out of range")

    adset = adsets[adset_index]

    # ---- Validation ----
    if not adset.creation_id:
        raise HTTPException(status_code=400, detail="Video not uploaded yet")
    if not adset.adset_id:
        raise HTTPException(status_code=400, detail="AdSet ID missing")
    if not adset.ad_account_id:
        raise HTTPException(status_code=400, detail="Ad account ID missing")

    ad_name = ad_name or adset.title

    act_ad_account_id = (
        adset.ad_account_id
        if adset.ad_account_id.startswith("act_")
        else f"act_{adset.ad_account_id}"
    )

    # ---- Create Creative ----
    creative_id = create_ig_reel_ad_creative(
        act_ad_account_id=act_ad_account_id,
        ad_name=ad_name,
        instagram_actor_id=INSTAGRAM_ACTOR_ID,
        video_id=adset.creation_id,  # TEMP — will become advideo ID later
        link=adset.link,page_id=PAGE_ID
    )

    # ---- Create Ad ----
    ad_url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{act_ad_account_id}/ads"

    ad_payload = {
        "name": ad_name,
        "adset_id": adset.adset_id,
        "creative": {
            "creative_id": creative_id
        },
        "status": "PAUSED",
        "access_token": ACCESS_TOKEN  # ✅ USER TOKEN
    }

    print("[DEBUG] Ad Payload:", ad_payload)

    ad_resp = requests.post(ad_url, json=ad_payload).json()

    if "error" in ad_resp:
        print("[ERROR] Ad creation failed:", ad_resp["error"])
        raise HTTPException(status_code=400, detail=ad_resp["error"])

    print("[INFO] Ad created:", ad_resp["id"])

    return {
        "message": "Instagram Reel ad created successfully",
        "ad_id": ad_resp["id"],
        "creative_id": creative_id,
        "adset_index": adset_index
    }
