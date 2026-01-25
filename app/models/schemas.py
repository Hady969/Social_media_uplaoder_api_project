from __future__ import annotations
from pydantic import BaseModel
from typing import Optional
from typing import Optional, Literal

class Campaign(BaseModel):
    ad_account_id: str
    name: str
    objective: str

class CreatedCampaign(Campaign):
    campaign_id: str
    page_id: str
    daily_budget: int = 1000
    optimization_goal: str ="REACH"


from pydantic import BaseModel

AssetType = Literal["video", "image"]

class AdSet(BaseModel):
    adset_id: str
    page_id: str
    campaign_id: str
    ad_account_id: str
    name: str
    daily_budget: int
    status: str = "PAUSED"
    link: str = "youtube.com"
    title: str = "Check this out!"


    asset_type: AssetType = "video"

    # existing optional fields
    video_id: str | None = None
    video_url: str | None = None
    creation_id: str | None = None
    instagram_post_id: str | None = None
    ad_id: Optional[str] = None

class AdSetCreate(BaseModel):
    campaign_id: str
    name: str = "Instagram Ad Set"
    page_id: str
    daily_budget: int = 1000
    optimization_goal: str = "REACH"

class AdUploadCreate(BaseModel):
    adset_id: str
    page_id: str
    instagram_actor_id: str
    link: str
    title: str = "Check this out!"
    name: str = "Instagram Video Ad"

class AdCreate(BaseModel):
    adset_id: str
    name: str = "Instagram Ad"
    page_id: str
    instagram_actor_id: str
    video_id: str
    link: str
    title: str = "Check this out!"

class InstagramUploadRequest(BaseModel):
    video_url: str
    caption: str | None = "Uploaded via API 🚀"

class InstagramPublishRequest(BaseModel):
    creation_id: str




from pydantic import BaseModel

class PaidAdRequest(BaseModel):
    ad_name: Optional[str]
    thumbnail_url: str  # required




from pydantic import BaseModel
from typing import Optional, List, Literal

CarouselItemType = Literal["image", "video"]

class CarouselItem(BaseModel):
    type: CarouselItemType
    url: str

class OrganicPost(BaseModel):
    title: str

    # video
    video_url: Optional[str] = None
    creation_id: Optional[str] = None
    instagram_post_id: Optional[str] = None

    # image
    image_url: Optional[str] = None

    # carousel
    carousel_items: Optional[List[CarouselItem]] = None
