from typing import List, Optional

from pydantic import BaseModel, Field

TONES = ["professional", "playful", "luxury", "minimal", "clear and practical"]
OUTPUT_FORMATS = ["plain", "html", "json", "csv"]
PROVIDERS = ["auto", "mock", "openai", "gemini"]
MODES = ["text-to-text", "text-to-image", "image-to-text", "image-to-image"]

IMAGE_STYLES = {
    "studio_white": "Clean e-commerce studio shot on a pure white background, soft shadow, centred, high detail",
    "lifestyle": "Natural lifestyle scene showing the product in everyday use, soft daylight, shallow depth of field",
    "luxury_dark": "Premium dark backdrop with dramatic rim lighting and subtle reflections",
    "minimal_pastel": "Minimal flat-lay on a soft pastel background with generous negative space",
    "vibrant_pop": "Bold colourful background, punchy contrast, playful social-media style",
}


class ProductFacts(BaseModel):
    """Seller input. Only name and category are required."""

    product_name: str = Field("", description="Names the item")
    category: str = Field("", description="Sets the product context")
    materials: str = ""
    size: str = Field("", description="Dimensions, capacity or weight")
    features: List[str] = Field(default_factory=list)
    target_audience: str = ""
    use_case: str = ""
    brand_voice: str = "professional"
    brand_voice_id: Optional[str] = None
    keyword: str = ""
    output_format: str = "plain"
    extra_notes: str = ""
    provider: str = "auto"


class ListingDraft(BaseModel):

    title: str
    description: str
    bullets: List[str]
    meta_description: str
    review_flags: List[str] = Field(default_factory=list)


class TextToImageRequest(BaseModel):
    facts: ProductFacts
    style: str = "studio_white"
    extra_prompt: str = ""


class ReviewRequest(BaseModel):

    approved: bool = False
    edited: Optional[ListingDraft] = None
    rating: Optional[int] = Field(None, ge=1, le=5)
    unsupported_claims: int = Field(0, ge=0)
    total_claims: int = Field(0, ge=0)
    seconds_to_approve: Optional[float] = Field(None, ge=0)
    notes: str = ""


class ExportRequest(BaseModel):
    draft: ListingDraft
    format: str = "plain"
    product_name: str = ""


class BrandVoice(BaseModel):

    name: str = Field(..., min_length=1, max_length=60)
    base_tone: str = "professional"
    rules: str = Field("", max_length=1500)
    example: str = Field("", max_length=1500)
