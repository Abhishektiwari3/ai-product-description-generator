"""FastAPI backend - the 'thin application layer' from slide 7.

Run:  uvicorn backend.main:app --reload --port 8000   (from the project root)
Then open http://localhost:8000
"""
from __future__ import annotations

import collections
import threading
import time
from pathlib import Path
from typing import Callable, Deque, Dict, List, Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import imaging
from .config import settings
from .db import Store
from .exports import export_draft
from .pipeline import (InputProblem, ResponseProblem, build_edit_prompt, build_image_prompt, build_prompt,
                       build_vision_prompt, check_response, finalize, normalize_facts, parse_model_json,
                       validate_facts)
from .providers import ProviderError, get_provider
from .schemas import (IMAGE_STYLES, MODES, OUTPUT_FORMATS, TONES, BrandVoice, ExportRequest, ProductFacts,
                      ReviewRequest, TextToImageRequest)

app = FastAPI(title="AI Product Description Generator", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=settings.allowed_origins, allow_methods=["*"], allow_headers=["*"])

store = Store(settings)

EXAMPLE = {  # Slide 12 - illustrative example only
    "product_name": "Reusable insulated bottle",
    "category": "Drinkware",
    "materials": "Stainless steel",
    "size": "",
    "features": ["Double-wall construction"],
    "target_audience": "",
    "use_case": "",
    "brand_voice": "clear and practical",
    "keyword": "insulated bottle",
    "output_format": "plain",
}


# ------------------------------------------------------------------ rate limiting (slide 7/10)
_hits: Dict[str, Deque[float]] = collections.defaultdict(collections.deque)
_lock = threading.Lock()


def rate_limit(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    t = time.time()
    with _lock:
        q = _hits[ip]
        while q and t - q[0] > 60:
            q.popleft()
        if len(q) >= settings.rate_limit_per_minute:
            raise HTTPException(429, detail={"code": "rate_limited",
                                             "message": "Too many requests. Please wait a minute and retry."})
        q.append(t)


# ------------------------------------------------------------------ shared runner
def run_mode(mode: str, provider_name: str, fn: Callable[[], dict]) -> dict:
    """Time the call, log minimal metadata, and turn every failure into a safe error (slide 8)."""
    t0 = time.perf_counter()
    try:
        out = fn()
    except InputProblem as e:
        store.log_request(mode, provider_name, (time.perf_counter() - t0) * 1000, "error", "invalid_input")
        raise HTTPException(422, detail={"code": "invalid_input", "message": "Please fix the highlighted fields.",
                                         "fields": e.errors})
    except imaging.ImageProblem as e:
        store.log_request(mode, provider_name, (time.perf_counter() - t0) * 1000, "error", "bad_image")
        raise HTTPException(400, detail={"code": "bad_image", "message": str(e)})
    except ProviderError as e:
        store.log_request(mode, provider_name, (time.perf_counter() - t0) * 1000, "error", e.code)
        raise HTTPException(e.status, detail={"code": e.code, "message": e.message})
    except ResponseProblem as e:
        store.log_request(mode, provider_name, (time.perf_counter() - t0) * 1000, "error", "bad_response")
        raise HTTPException(502, detail={"code": "bad_response",
                                         "message": f"{e} Your inputs are kept - please retry."})
    latency = (time.perf_counter() - t0) * 1000
    store.log_request(mode, provider_name, latency, "ok")
    out["latency_ms"] = round(latency, 1)
    return out


@app.exception_handler(HTTPException)
async def http_error(_request: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "error", "message": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content={"error": detail})


@app.exception_handler(RequestValidationError)
async def bad_request(_request: Request, exc: RequestValidationError):
    fields = {}
    for e in exc.errors():
        loc = [str(x) for x in e.get("loc", []) if x not in ("body", "facts")]
        fields[loc[-1] if loc else "request"] = e.get("msg", "Invalid value.")
    return JSONResponse(status_code=422, content={"error": {"code": "invalid_input",
                                                            "message": "Please fix the highlighted fields.",
                                                            "fields": fields}})


@app.exception_handler(Exception)
async def unexpected(_request: Request, _exc: Exception):  # never leak stack traces
    return JSONResponse(status_code=500, content={"error": {"code": "server_error",
                                                            "message": "Something went wrong on the server."}})


def _features_from_text(text: str) -> List[str]:
    return [p for p in (text or "").replace(",", "\n").split("\n") if p.strip()]


async def _read_upload(upload: UploadFile) -> bytes:
    data = await upload.read()
    img = imaging.load_image(data, settings.max_image_mb)  # validates type + size
    return imaging.to_png(img)


# ------------------------------------------------------------------ info
@app.get("/api/health")
def health():
    return {"status": "ok", "db": store.backend, "providers": settings.available_providers(),
            "default_provider": settings.resolve_provider(None)}


@app.get("/api/meta")
def meta():
    return {"tones": TONES, "formats": OUTPUT_FORMATS, "styles": IMAGE_STYLES, "modes": MODES,
            "providers": settings.available_providers(), "default_provider": settings.resolve_provider(None),
            "example": EXAMPLE, "max_image_mb": settings.max_image_mb}


# ------------------------------------------------------------------ 1. text -> text
@app.post("/api/generate/text-to-text")
def text_to_text(facts: ProductFacts, request: Request):
    rate_limit(request)
    pname = settings.resolve_provider(facts.provider)

    def work():
        validate_facts(facts)
        f = normalize_facts(facts)
        voice = store.get_voice(f.brand_voice_id)
        prompt = build_prompt(f, voice)
        provider = get_provider(pname, settings)
        draft = finalize(check_response(parse_model_json(provider.generate_listing(prompt, f))), f)
        content, _, _ = export_draft(draft, f.output_format, f.product_name)
        gid = store.save_generation({"mode": "text-to-text", "provider": pname, "model": provider.text_model,
                                     "input": f.model_dump(), "output": draft.model_dump(), "image_ids": []})
        return {"id": gid, "mode": "text-to-text", "provider": pname, "model": provider.text_model,
                "draft": draft.model_dump(), "export_preview": content, "prompt": prompt}

    return run_mode("text-to-text", pname, work)


# ------------------------------------------------------------------ 2. text -> image
@app.post("/api/generate/text-to-image")
def text_to_image(body: TextToImageRequest, request: Request):
    rate_limit(request)
    pname = settings.resolve_provider(body.facts.provider)

    def work():
        if body.style not in IMAGE_STYLES:
            raise InputProblem({"style": f"Choose one of: {', '.join(IMAGE_STYLES)}."})
        if len(body.extra_prompt) > 500:
            raise InputProblem({"extra_prompt": "Keep this under 500 characters."})
        validate_facts(body.facts)
        f = normalize_facts(body.facts)
        prompt = build_image_prompt(f, body.style, body.extra_prompt)
        provider = get_provider(pname, settings)
        png = imaging.to_png(imaging.load_image(provider.generate_image(prompt, f, body.style), 25))
        iid = store.save_image(png, "generated")
        gid = store.save_generation({"mode": "text-to-image", "provider": pname, "model": provider.image_model,
                                     "input": {**f.model_dump(), "style": body.style, "extra_prompt": body.extra_prompt},
                                     "output": {"image_id": iid, "prompt": prompt}, "image_ids": [iid]})
        return {"id": gid, "mode": "text-to-image", "provider": pname, "model": provider.image_model,
                "image_url": f"/api/images/{iid}", "prompt": prompt,
                "review_flags": ["Generated images are illustrations. Do not use them as proof of the real product's "
                                 "appearance - compare against the actual item before publishing."]}

    return run_mode("text-to-image", pname, work)


# ------------------------------------------------------------------ 3. image -> text
@app.post("/api/generate/image-to-text")
async def image_to_text(request: Request, image: UploadFile = File(...), product_name: str = Form(""),
                        category: str = Form(""), materials: str = Form(""), size: str = Form(""),
                        features: str = Form(""), target_audience: str = Form(""),
                        brand_voice: str = Form("professional"), keyword: str = Form(""),
                        output_format: str = Form("plain"), provider: str = Form("auto")):
    rate_limit(request)
    pname = settings.resolve_provider(provider)
    try:
        png = await _read_upload(image)
    except imaging.ImageProblem as e:
        store.log_request("image-to-text", pname, 0, "error", "bad_image")
        raise HTTPException(400, detail={"code": "bad_image", "message": str(e)})
    hints = ProductFacts(product_name=product_name, category=category, materials=materials, size=size,
                         features=_features_from_text(features), target_audience=target_audience,
                         brand_voice=brand_voice, keyword=keyword, output_format=output_format, provider=provider)

    def work():
        validate_facts(hints, require_name=False)
        f = normalize_facts(hints)
        prov = get_provider(pname, settings)
        prompt = build_vision_prompt(f)
        draft = check_response(parse_model_json(prov.describe_image(prompt, png, f)))
        # numbers seen in the photo cannot be checked against facts, so flag them but keep missing-fact checks
        draft = finalize(draft, f, check_numbers=True)
        analysis = imaging.analyze(imaging.load_image(png))
        content, _, _ = export_draft(draft, f.output_format, f.product_name)
        iid = store.save_image(png, "upload")
        gid = store.save_generation({"mode": "image-to-text", "provider": pname, "model": prov.text_model,
                                     "input": {**f.model_dump(), "image_id": iid}, "output": draft.model_dump(),
                                     "analysis": analysis, "image_ids": [iid]})
        return {"id": gid, "mode": "image-to-text", "provider": pname, "model": prov.text_model,
                "draft": draft.model_dump(), "analysis": analysis, "export_preview": content,
                "input_image_url": f"/api/images/{iid}"}

    return run_mode("image-to-text", pname, work)


# ------------------------------------------------------------------ 4. image -> image
@app.post("/api/generate/image-to-image")
async def image_to_image(request: Request, image: UploadFile = File(...), style: str = Form("studio_white"),
                         extra_prompt: str = Form(""), provider: str = Form("auto")):
    rate_limit(request)
    pname = settings.resolve_provider(provider)
    try:
        png = await _read_upload(image)
    except imaging.ImageProblem as e:
        store.log_request("image-to-image", pname, 0, "error", "bad_image")
        raise HTTPException(400, detail={"code": "bad_image", "message": str(e)})

    def work():
        if style not in IMAGE_STYLES:
            raise InputProblem({"style": f"Choose one of: {', '.join(IMAGE_STYLES)}."})
        if len(extra_prompt) > 500:
            raise InputProblem({"extra_prompt": "Keep this under 500 characters."})
        prov = get_provider(pname, settings)
        prompt = build_edit_prompt(style, extra_prompt)
        out_png = imaging.to_png(imaging.load_image(prov.edit_image(png, prompt, style), 25))
        in_id = store.save_image(png, "upload")
        out_id = store.save_image(out_png, "edited")
        gid = store.save_generation({"mode": "image-to-image", "provider": pname, "model": prov.image_model,
                                     "input": {"image_id": in_id, "style": style, "extra_prompt": extra_prompt},
                                     "output": {"image_id": out_id, "prompt": prompt}, "image_ids": [in_id, out_id]})
        return {"id": gid, "mode": "image-to-image", "provider": pname, "model": prov.image_model,
                "input_image_url": f"/api/images/{in_id}", "image_url": f"/api/images/{out_id}", "prompt": prompt,
                "review_flags": ["Check that the product itself (shape, colour, labels) was not altered before publishing."]}

    return run_mode("image-to-image", pname, work)


# ------------------------------------------------------------------ images, history, review, export
@app.get("/api/images/{image_id}")
def get_image(image_id: str):
    data = store.get_image(image_id)
    if data is None:
        raise HTTPException(404, detail={"code": "not_found", "message": "Image not found."})
    return Response(content=data, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})


@app.get("/api/history")
def history(mode: Optional[str] = None, limit: int = 50):
    return {"items": store.list_generations(mode, min(max(limit, 1), 200))}


@app.get("/api/history/{gid}")
def history_item(gid: str):
    d = store.get_generation(gid)
    if not d:
        raise HTTPException(404, detail={"code": "not_found", "message": "Generation not found."})
    return d


@app.delete("/api/history/{gid}")
def delete_item(gid: str):
    if not store.delete_generation(gid):
        raise HTTPException(404, detail={"code": "not_found", "message": "Generation not found."})
    return {"deleted": True}


@app.post("/api/history/{gid}/review")
def review(gid: str, body: ReviewRequest):
    if body.unsupported_claims > body.total_claims and body.total_claims:
        raise HTTPException(422, detail={"code": "invalid_input",
                                         "message": "Unsupported claims cannot exceed total claims."})
    payload = body.model_dump()
    if body.edited:
        payload["edited"] = body.edited.model_dump()
    if not store.review_generation(gid, payload):
        raise HTTPException(404, detail={"code": "not_found", "message": "Generation not found."})
    return {"saved": True}


@app.post("/api/export")
def export(body: ExportRequest):
    if body.format not in OUTPUT_FORMATS:
        raise HTTPException(422, detail={"code": "invalid_input", "message": f"Format must be one of {OUTPUT_FORMATS}."})
    content, mime, ext = export_draft(body.draft, body.format, body.product_name)
    slug = "".join(c if c.isalnum() else "-" for c in (body.product_name or "listing").lower()).strip("-")[:40]
    return Response(content=content, media_type=mime,
                    headers={"Content-Disposition": f'attachment; filename="{slug or "listing"}.{ext}"'})


# ------------------------------------------------------------------ brand voices
@app.get("/api/voices")
def voices():
    return {"items": store.list_voices()}


@app.post("/api/voices")
def add_voice(v: BrandVoice):
    if v.base_tone not in TONES:
        raise HTTPException(422, detail={"code": "invalid_input", "message": f"Base tone must be one of {TONES}."})
    return {"id": store.save_voice(v.model_dump())}


@app.delete("/api/voices/{vid}")
def remove_voice(vid: str):
    if not store.delete_voice(vid):
        raise HTTPException(404, detail={"code": "not_found", "message": "Voice not found."})
    return {"deleted": True}


# ------------------------------------------------------------------ evaluation (slides 13-14)
@app.get("/api/metrics")
def metrics():
    return store.metrics()


# ------------------------------------------------------------------ frontend
FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
if FRONTEND.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND), html=True), name="frontend")
