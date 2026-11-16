from __future__ import annotations

import base64
import json
import time
from typing import Dict, List, Optional

import httpx

from . import imaging
from .config import Settings
from .pipeline import LISTING_JSON_SCHEMA
from .schemas import ProductFacts


class ProviderError(Exception):
    """Safe, user-facing error. `code` is one of: timeout, rate_limited, auth, upstream, bad_response, config."""

    def __init__(self, code: str, message: str, status: int = 502):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


# ------------------------------------------------------------------ HTTP helper
def _post(settings: Settings, url: str, *, headers: dict, json_body: Optional[dict] = None,
          data: Optional[dict] = None, files: Optional[dict] = None) -> dict:
    """POST with timeout and bounded retries on timeouts, 429 and 5xx."""
    last: Optional[ProviderError] = None
    for attempt in range(settings.max_retries + 1):
        try:
            with httpx.Client(timeout=settings.request_timeout_s) as client:
                r = client.post(url, headers=headers, json=json_body, data=data, files=files)
        except httpx.TimeoutException:
            last = ProviderError("timeout", "The AI provider took too long to respond. Please retry.", 504)
        except httpx.HTTPError:
            last = ProviderError("upstream", "Could not reach the AI provider. Check your connection.", 502)
        else:
            if r.status_code in (401, 403):
                raise ProviderError("auth", "The AI provider rejected the API key. Check server configuration.", 502)
            if r.status_code == 429:
                body = r.text.lower()
                if "limit: 0" in body or "insufficient_quota" in body or "billing" in body:
                    raise ProviderError("quota", "Your AI provider plan has no quota for this model. Turn on billing "
                                        "for your API key (Google AI Studio or OpenAI Billing), then try again.", 429)
                last = ProviderError("rate_limited", "The AI provider is rate-limiting requests. Try again shortly.", 429)
            elif r.status_code >= 500:
                last = ProviderError("upstream", "The AI provider had a temporary error. Please retry.", 502)
            elif r.status_code >= 400:
                hint = ""
                try:
                    hint = (r.json().get("error") or {}).get("message", "")[:160]
                except Exception:
                    pass
                raise ProviderError("upstream", f"The AI provider refused the request. {hint}".strip(), 502)
            else:
                return r.json()
        if attempt < settings.max_retries:
            time.sleep(min(8.0, 1.5 ** attempt))
    assert last is not None
    raise last


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


# ------------------------------------------------------------------ base
class BaseProvider:
    def __init__(self, settings: Settings):
        self.settings = settings

    def generate_listing(self, prompt: Dict[str, str], facts: ProductFacts) -> str:
        raise NotImplementedError

    def describe_image(self, prompt: Dict[str, str], image_png: bytes, hints: ProductFacts) -> str:
        raise NotImplementedError

    def generate_image(self, prompt: str, facts: ProductFacts, style: str) -> bytes:
        raise NotImplementedError

    def edit_image(self, image_png: bytes, prompt: str, style: str) -> bytes:
        raise NotImplementedError

    @property
    def text_model(self) -> str:
        return "-"

    @property
    def image_model(self) -> str:
        return "-"


# ------------------------------------------------------------------ OpenAI
class OpenAIProvider(BaseProvider):
    base = "https://api.openai.com/v1"

    @property
    def text_model(self) -> str:
        return self.settings.openai_text_model

    @property
    def image_model(self) -> str:
        return self.settings.openai_image_model

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.settings.openai_api_key}"}

    def _chat(self, system: str, user_content) -> str:
        body = {
            "model": self.text_model,
            "temperature": 0.4,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user_content}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "listing_draft", "strict": True, "schema": LISTING_JSON_SCHEMA},
            },
        }
        res = _post(self.settings, f"{self.base}/chat/completions", headers=self._headers(), json_body=body)
        try:
            msg = res["choices"][0]["message"]
        except (KeyError, IndexError):
            raise ProviderError("bad_response", "The AI provider returned an empty answer.")
        if msg.get("refusal"):
            raise ProviderError("bad_response", "The model declined this request.")
        return msg.get("content") or ""

    def generate_listing(self, prompt, facts):
        return self._chat(prompt["system"], prompt["user"])

    def describe_image(self, prompt, image_png, hints):
        content = [
            {"type": "text", "text": prompt["user"]},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_b64(image_png)}"}},
        ]
        return self._chat(prompt["system"], content)

    def _image_from(self, res: dict) -> bytes:
        try:
            item = res["data"][0]
        except (KeyError, IndexError):
            raise ProviderError("bad_response", "The image provider returned no image.")
        if item.get("b64_json"):
            return base64.b64decode(item["b64_json"])
        if item.get("url"):
            with httpx.Client(timeout=self.settings.request_timeout_s) as c:
                return c.get(item["url"]).content
        raise ProviderError("bad_response", "The image provider returned no image.")

    def generate_image(self, prompt, facts, style):
        body = {"model": self.image_model, "prompt": prompt, "size": "1024x1024", "n": 1}
        if self.image_model.startswith("dall-e"):
            body["response_format"] = "b64_json"
        res = _post(self.settings, f"{self.base}/images/generations", headers=self._headers(), json_body=body)
        return self._image_from(res)

    def edit_image(self, image_png, prompt, style):
        files = {"image": ("product.png", image_png, "image/png")}
        data = {"model": self.image_model, "prompt": prompt, "size": "1024x1024", "n": "1"}
        res = _post(self.settings, f"{self.base}/images/edits", headers=self._headers(), data=data, files=files)
        return self._image_from(res)


# ------------------------------------------------------------------ Gemini
GEMINI_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "title": {"type": "STRING"},
        "description": {"type": "STRING"},
        "bullets": {"type": "ARRAY", "items": {"type": "STRING"}},
        "meta_description": {"type": "STRING"},
        "review_flags": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["title", "description", "bullets", "meta_description", "review_flags"],
}


class GeminiProvider(BaseProvider):
    base = "https://generativelanguage.googleapis.com/v1beta/models"

    @property
    def text_model(self) -> str:
        return self.settings.gemini_text_model

    @property
    def image_model(self) -> str:
        return self.settings.gemini_image_model

    def _headers(self) -> dict:
        return {"x-goog-api-key": self.settings.gemini_api_key, "Content-Type": "application/json"}

    def _generate(self, model: str, parts: List[dict], system: Optional[str] = None, json_mode: bool = False,
                  image_out: bool = False) -> dict:
        body: dict = {"contents": [{"role": "user", "parts": parts}]}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        cfg: dict = {}
        if json_mode:
            cfg.update({"responseMimeType": "application/json", "responseSchema": GEMINI_SCHEMA, "temperature": 0.4})
        if image_out:
            cfg["responseModalities"] = ["TEXT", "IMAGE"]
        if cfg:
            body["generationConfig"] = cfg
        return _post(self.settings, f"{self.base}/{model}:generateContent", headers=self._headers(), json_body=body)

    @staticmethod
    def _parts(res: dict) -> List[dict]:
        try:
            return res["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError):
            reason = (res.get("promptFeedback") or {}).get("blockReason")
            raise ProviderError("bad_response", "The model returned no content" + (f" ({reason})." if reason else "."))

    def _text(self, res: dict) -> str:
        return "".join(p.get("text", "") for p in self._parts(res))

    def _image(self, res: dict) -> bytes:
        for p in self._parts(res):
            inline = p.get("inlineData") or p.get("inline_data")
            if inline and inline.get("data"):
                return base64.b64decode(inline["data"])
        raise ProviderError("bad_response", "The image model returned text but no image. Try a clearer prompt.")

    def generate_listing(self, prompt, facts):
        return self._text(self._generate(self.text_model, [{"text": prompt["user"]}], prompt["system"], json_mode=True))

    def describe_image(self, prompt, image_png, hints):
        parts = [{"inlineData": {"mimeType": "image/png", "data": _b64(image_png)}}, {"text": prompt["user"]}]
        return self._text(self._generate(self.text_model, parts, prompt["system"], json_mode=True))

    def generate_image(self, prompt, facts, style):
        return self._image(self._generate(self.image_model, [{"text": prompt}], image_out=True))

    def edit_image(self, image_png, prompt, style):
        parts = [{"inlineData": {"mimeType": "image/png", "data": _b64(image_png)}}, {"text": prompt}]
        return self._image(self._generate(self.image_model, parts, image_out=True))


# ------------------------------------------------------------------ Mock (offline)
def _cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


def _join(items: List[str]) -> str:
    items = [i.rstrip(".") for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


class MockProvider(BaseProvider):
    """Deterministic, template-based drafts. Never invents numbers or claims."""

    @property
    def text_model(self) -> str:
        return "offline-template-v1"

    @property
    def image_model(self) -> str:
        return "offline-pillow-v1"

    def _listing(self, f: ProductFacts, visual_note: str = "") -> dict:
        name = f.product_name or "product"
        nl = name.lower()
        mat = f.materials.lower()
        mat_phrase = f"made from {mat}" if mat and mat not in nl else ""
        feats = [x[:1].lower() + x[1:] for x in f.features]
        feat_phrase = f"with {_join(feats[:3]).lower()}" if feats else ""
        cat = f.category.lower()
        core = " ".join(p for p in [mat_phrase, feat_phrase] if p)

        tone = f.brand_voice
        if tone == "playful":
            opener = f"Meet your new favourite {cat or 'pick'}: the {nl}{(' ' + core) if core else ''}!"
        elif tone == "luxury":
            opener = (f"Crafted from {mat}, the {nl} brings quiet refinement" + (f", {feat_phrase}" if feat_phrase else "") + ".") \
                if mat else f"The {nl} brings quiet refinement" + (f", {feat_phrase}" if feat_phrase else "") + "."
        elif tone == "minimal":
            opener = ". ".join(p for p in [_cap(name), _cap(mat) if mat else "", _cap(_join(feats[:3]))] if p) + "."
        elif tone == "clear and practical":
            opener = f"A {nl}{(' ' + core) if core else ''}."
        elif cat and cat not in nl:
            opener = f"The {nl}{(' ' + core) if core else ''}, from our {cat} range."
        else:
            opener = f"A well-made {nl}{(' ' + core) if core else ''}."
        sentences = [_cap(opener)]
        if visual_note:
            sentences.append(visual_note)
        if f.target_audience:
            sentences.append(f"Designed for {f.target_audience.rstrip('.')}.")
        if f.use_case:
            sentences.append(f"Suited to {f.use_case.rstrip('.')}.")
        if f.size:
            sentences.append(f"Size: {f.size.rstrip('.')}.")
        description = " ".join(sentences)

        if f.keyword and f.keyword.lower() not in description.lower() and f.keyword.lower() not in nl:
            description += f" A practical choice if you are looking for an {f.keyword.lower()}." \
                if f.keyword[0].lower() in "aeiou" else f" A practical choice if you are looking for a {f.keyword.lower()}."

        title_bits = [name]
        if mat and mat not in nl:
            title_bits = [f"{_cap(mat)} {nl}"]
        if f.keyword and f.keyword.lower() not in title_bits[0].lower():
            title_bits.append(f"| {f.keyword}")
        title = " ".join(title_bits)

        bullets = [_cap(x).rstrip(".") + ("" if tone == "minimal" else " for everyday use") + "." for x in f.features[:5]]
        if mat and not any(mat in b.lower() for b in bullets):
            bullets.append(f"Made from {mat}.")
        if f.size:
            bullets.append(f"Size: {f.size}.")
        if not bullets:
            bullets.append(f"{_cap(cat or 'product')} - add features to strengthen this listing.")

        meta = f"{_cap(nl)}{(' ' + core) if core else ''}."
        if f.keyword and f.keyword.lower() not in meta.lower():
            meta = f"{_cap(f.keyword)}: " + meta
        return {"title": title, "description": description, "bullets": bullets, "meta_description": meta,
                "review_flags": []}

    def generate_listing(self, prompt, facts):
        return json.dumps(self._listing(facts))

    def describe_image(self, prompt, image_png, hints):
        img = imaging.load_image(image_png)
        a = imaging.analyze(img)
        colours = _join(a["dominant_colours"])
        f = hints.model_copy()
        if not f.product_name:
            f.product_name = f"{_cap(a['dominant_colours'][0])} {(hints.category or 'product').lower()}"
        note = f"Shown in {colours} tones, photographed on a {a['background']} background ({a['orientation']} shot)."
        data = self._listing(f, visual_note=note)
        data["review_flags"] = [
            "Offline mode: only colours and composition were read from the photo. Product type, material and size are NOT verified.",
        ]
        if not hints.product_name:
            data["review_flags"].append("Product name was guessed from colour - replace it with the real name.")
        return json.dumps(data)

    def generate_image(self, prompt, facts, style):
        return imaging.to_png(imaging.render_concept(facts.product_name, facts.category, facts.features, style))

    def edit_image(self, image_png, prompt, style):
        return imaging.to_png(imaging.restyle(imaging.load_image(image_png), style))


def get_provider(name: str, settings: Settings) -> BaseProvider:
    if name == "openai":
        if not settings.openai_api_key:
            raise ProviderError("config", "OpenAI is not configured. Add OPENAI_API_KEY to the server .env file.", 400)
        return OpenAIProvider(settings)
    if name == "gemini":
        if not settings.gemini_api_key:
            raise ProviderError("config", "Gemini is not configured. Add GEMINI_API_KEY to the server .env file.", 400)
        return GeminiProvider(settings)
    return MockProvider(settings)
