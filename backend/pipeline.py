"""The request pipeline from slide 8:

01 Validate -> 02 Normalize -> 03 Build prompt -> 04 Call model -> 05 Check response -> 06 Return draft

Step 04 lives in providers.py so provider code stays separate from UI code (slide 10).
"""
from __future__ import annotations

import json
import re
from typing import Dict, List, Optional

from pydantic import ValidationError

from .schemas import IMAGE_STYLES, OUTPUT_FORMATS, PROVIDERS, TONES, ListingDraft, ProductFacts

LIMITS = {
    "product_name": 120,
    "category": 80,
    "materials": 300,
    "size": 120,
    "target_audience": 200,
    "use_case": 300,
    "keyword": 80,
    "extra_notes": 1000,
    "feature_item": 200,
    "feature_count": 15,
}

# JSON schema sent to the model (slide 9 "example response shape").
LISTING_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "bullets": {"type": "array", "items": {"type": "string"}},
        "meta_description": {"type": "string"},
        "review_flags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "description", "bullets", "meta_description", "review_flags"],
    "additionalProperties": False,
}


class InputProblem(Exception):
    """Raised when seller input fails validation. Carries field -> message."""

    def __init__(self, errors: Dict[str, str]):
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors


class ResponseProblem(Exception):
    """Raised when the model's answer does not match the schema."""


# ---------------------------------------------------------------- 01 validate
def validate_facts(facts: ProductFacts, require_name: bool = True) -> None:
    errors: Dict[str, str] = {}
    if require_name and not facts.product_name.strip():
        errors["product_name"] = "Product name is required."
    if require_name and not facts.category.strip():
        errors["category"] = "Category is required."
    for f in ["product_name", "category", "materials", "size", "target_audience", "use_case", "keyword", "extra_notes"]:
        if len(getattr(facts, f) or "") > LIMITS[f]:
            errors[f] = f"Keep this under {LIMITS[f]} characters."
    if len(facts.features) > LIMITS["feature_count"]:
        errors["features"] = f"Use at most {LIMITS['feature_count']} features."
    elif any(len(x) > LIMITS["feature_item"] for x in facts.features):
        errors["features"] = f"Each feature must be under {LIMITS['feature_item']} characters."
    if facts.brand_voice.lower() not in TONES:
        errors["brand_voice"] = f"Choose one of: {', '.join(TONES)}."
    if facts.output_format.lower() not in OUTPUT_FORMATS:
        errors["output_format"] = f"Choose one of: {', '.join(OUTPUT_FORMATS)}."
    if facts.provider.lower() not in PROVIDERS:
        errors["provider"] = f"Choose one of: {', '.join(PROVIDERS)}."
    if errors:
        raise InputProblem(errors)


# ---------------------------------------------------------------- 02 normalize
def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def normalize_facts(facts: ProductFacts) -> ProductFacts:
    """Trim whitespace, split comma/newline feature lists, drop duplicates."""
    features: List[str] = []
    seen = set()
    for raw in facts.features:
        for part in re.split(r"[\n;]+", raw or ""):
            item = _clean(part).strip("-•* ").strip()
            if item and item.lower() not in seen:
                seen.add(item.lower())
                features.append(item)
    data = facts.model_dump()
    for k in ["product_name", "category", "materials", "size", "target_audience", "use_case", "keyword", "extra_notes"]:
        data[k] = _clean(data[k])
    data["features"] = features
    data["brand_voice"] = data["brand_voice"].lower()
    data["output_format"] = data["output_format"].lower()
    data["provider"] = data["provider"].lower()
    return ProductFacts(**data)


def facts_block(facts: ProductFacts) -> str:
    rows = [
        ("Product name", facts.product_name),
        ("Category", facts.category),
        ("Materials", facts.materials),
        ("Size / capacity", facts.size),
        ("Features", "; ".join(facts.features)),
        ("Target audience", facts.target_audience),
        ("Use case", facts.use_case),
        ("Extra notes", facts.extra_notes),
    ]
    return "\n".join(f"- {k}: {v if v else '(not supplied)'}" for k, v in rows)


# ---------------------------------------------------------------- 03 build prompt
SYSTEM_RULES = """You write e-commerce product listing drafts for a seller who will review them.
Rules (instruction layer):
1. Treat the user-provided attributes as the only source of truth.
2. Write for the requested audience and selected brand voice.
3. Use the target keyword naturally, at most twice; never repeat it just to hit a count.
4. Omit unsupported claims. Do NOT invent sizes, capacities, durations, certifications, ratings, prices or performance numbers.
5. If an important fact is missing (e.g. size, material), add a short note to review_flags instead of guessing.
6. Title <= 80 characters. meta_description <= 155 characters. 3-6 bullets.
Return ONLY JSON matching the schema."""

TONE_GUIDE = {
    "professional": "Confident, precise and trustworthy. No slang.",
    "playful": "Friendly and upbeat with light energy; still factual.",
    "luxury": "Refined, elegant and calm; focus on craft and material.",
    "minimal": "Short sentences, few adjectives, essentials only.",
    "clear and practical": "Plain language focused on everyday use.",
}


def build_prompt(facts: ProductFacts, voice: Optional[dict] = None) -> Dict[str, str]:
    tone = facts.brand_voice
    voice_txt = f"Brand voice: {tone} - {TONE_GUIDE.get(tone, '')}"
    if voice:
        voice_txt += f"\nSaved brand voice '{voice.get('name')}': {voice.get('rules', '')}"
        if voice.get("example"):
            voice_txt += f"\nExample of this voice (style only, not facts): {voice['example']}"
    kw = f"Target keyword (optional, use naturally): {facts.keyword}" if facts.keyword else "Target keyword: none"
    user = (
        f"PRODUCT FACTS\n{facts_block(facts)}\n\n{voice_txt}\n{kw}\n"
        f"Destination format: {facts.output_format}\n\n"
        "Write the listing draft as JSON with: title, description, bullets, meta_description, review_flags."
    )
    return {"system": SYSTEM_RULES, "user": user}


def build_vision_prompt(hints: ProductFacts) -> Dict[str, str]:
    """Image -> text: describe only what is visible, plus any facts the seller typed."""
    system = SYSTEM_RULES + (
        "\nYou are given a product photo. Describe only what is clearly visible "
        "(type of item, colour, shape, visible parts). Anything not visible and not in the facts "
        "(material, size, capacity, performance) must go into review_flags as 'not verified'."
    )
    user = (
        f"Seller facts (may be partial):\n{facts_block(hints)}\n"
        f"Brand voice: {hints.brand_voice} - {TONE_GUIDE.get(hints.brand_voice, '')}\n"
        f"Target keyword: {hints.keyword or 'none'}\n"
        "Write the listing draft JSON from the photo and these facts."
    )
    return {"system": system, "user": user}


def build_image_prompt(facts: ProductFacts, style: str, extra: str = "") -> str:
    """Text -> image prompt. Visual-only, built from supplied facts."""
    style_txt = IMAGE_STYLES.get(style, IMAGE_STYLES["studio_white"])
    parts = [f"Product photograph of a {facts.product_name}".strip()]
    if facts.category:
        parts.append(f"({facts.category})")
    if facts.materials:
        parts.append(f"made of {facts.materials}")
    if facts.features:
        parts.append("showing " + ", ".join(facts.features[:4]))
    prompt = " ".join(parts) + f". Style: {style_txt}."
    if extra:
        prompt += f" {extra.strip()}"
    prompt += " No text, no logos, no watermarks. Realistic proportions."
    return prompt


def build_edit_prompt(style: str, extra: str = "") -> str:
    style_txt = IMAGE_STYLES.get(style, IMAGE_STYLES["studio_white"])
    p = (
        "Keep the exact same product (shape, colour, details, labels) and change only the presentation. "
        f"New presentation: {style_txt}."
    )
    if extra:
        p += f" {extra.strip()}"
    return p + " Do not add text or logos."


# ---------------------------------------------------------------- 05 check response
def parse_model_json(raw: str) -> dict:
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    raise ResponseProblem("The model did not return valid JSON.")


def _trim(s: str, n: int) -> str:
    s = _clean(s)
    if len(s) <= n:
        return s
    cut = s[:n].rsplit(" ", 1)[0]
    return cut.rstrip(",.;:") + "…"


def check_response(data: dict) -> ListingDraft:
    """Structure check only - 'schema validation checks structure, not factual truth' (slide 9)."""
    try:
        draft = ListingDraft(**data)
    except ValidationError as e:
        raise ResponseProblem(f"Response failed schema validation: {e.errors()[0]['msg']}") from e
    if not draft.title.strip() or not draft.description.strip():
        raise ResponseProblem("Response is missing a title or description.")
    bullets = [_clean(b).lstrip("-•* ") for b in draft.bullets if _clean(b)]
    if not bullets:
        raise ResponseProblem("Response has no feature bullets.")
    return ListingDraft(
        title=_trim(draft.title, 100),
        description=draft.description.strip(),
        bullets=bullets[:8],
        meta_description=_trim(draft.meta_description or draft.description, 160),
        review_flags=[_clean(f) for f in draft.review_flags if _clean(f)],
    )


# ---------------------------------------------------------------- guardrail: unsupported claims
RISKY_PHRASES = [
    "waterproof", "water-resistant", "bpa-free", "bpa free", "leak-proof", "leakproof", "dishwasher safe",
    "microwave safe", "organic", "eco-friendly", "sustainable", "recyclable", "biodegradable", "hypoallergenic",
    "certified", "fda", "ce marked", "clinically", "guarantee", "warranty", "lifetime", "best", "#1", "number one",
    "award-winning", "unbreakable", "indestructible", "keeps drinks", "all day", "24 hours", "12 hours",
    "vegan", "non-toxic", "medical", "cures", "doctor recommended",
]
NUMBER_RE = re.compile(r"\b\d+(?:[.,]\d+)?\s?(?:%|ml|l|oz|g|kg|lb|lbs|cm|mm|m|in|inch|inches|hours?|hrs?|days?|years?|w|mah|gb|tb|x)?\b", re.I)


def find_unsupported_claims(draft: ListingDraft, facts: ProductFacts) -> List[str]:
    """Heuristic check: numbers and risky phrases that appear in the draft but not in the facts."""
    source = " ".join(
        [facts.product_name, facts.category, facts.materials, facts.size, " ".join(facts.features),
         facts.target_audience, facts.use_case, facts.extra_notes, facts.keyword]
    ).lower()
    out_text = " ".join([draft.title, draft.description, " ".join(draft.bullets), draft.meta_description]).lower()
    flags: List[str] = []
    seen = set()
    for m in NUMBER_RE.finditer(out_text):
        token = m.group(0).strip()
        num = re.match(r"\d+(?:[.,]\d+)?", token).group(0)
        if num not in source and token not in seen:
            seen.add(token)
            flags.append(f"Unsupported number '{token}' is not in the product facts - verify or remove.")
    for phrase in RISKY_PHRASES:
        if phrase in out_text and phrase not in source:
            flags.append(f"Claim '{phrase}' is not supported by the supplied facts - verify or remove.")
    return flags


def missing_fact_flags(facts: ProductFacts) -> List[str]:
    flags = []
    if not facts.size:
        flags.append("Size / capacity was not supplied. Do not add it.")
    if not facts.materials:
        flags.append("Material was not supplied. Do not state one.")
    if not facts.features:
        flags.append("No features supplied - bullets may be thin.")
    return flags


def keyword_flags(draft: ListingDraft, keyword: str) -> List[str]:
    if not keyword:
        return []
    text = " ".join([draft.title, draft.description, " ".join(draft.bullets)]).lower()
    n = text.count(keyword.lower())
    if n == 0:
        return [f"Keyword '{keyword}' was not used - add it only if it fits naturally."]
    if n > 3:
        return [f"Keyword '{keyword}' appears {n} times - reduce repetition."]
    return []


def finalize(draft: ListingDraft, facts: ProductFacts, check_numbers: bool = True) -> ListingDraft:
    """Merge model flags with our own guardrail flags (deduplicated)."""
    flags = list(draft.review_flags)
    flags += missing_fact_flags(facts)
    if check_numbers:
        flags += find_unsupported_claims(draft, facts)
    flags += keyword_flags(draft, facts.keyword)
    dedup: List[str] = []
    for f in flags:
        if f.lower() not in {d.lower() for d in dedup}:
            dedup.append(f)
    return draft.model_copy(update={"review_flags": dedup})
