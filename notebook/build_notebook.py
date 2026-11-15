"""Builds AI_Product_Description_Generator.ipynb from the backend source.

The notebook is self-contained (no import of the backend package) but its code
cells are copied from backend/*.py, so the notebook and the web app never drift.

    python notebook/build_notebook.py
"""
import re
from pathlib import Path

import nbformat as nbf

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
OUT = Path(__file__).resolve().parent / "AI_Product_Description_Generator.ipynb"

cells = []


def md(s):
    cells.append(nbf.v4.new_markdown_cell(s.strip()))


def code(s):
    cells.append(nbf.v4.new_code_cell(s.strip()))


def module(name):
    src = (BACKEND / f"{name}.py").read_text()
    src = re.sub(r"^from __future__ import annotations\n", "", src, flags=re.M)
    src = re.sub(r"^from \.\w* import \([^)]*\)\n", "", src, flags=re.M | re.S)
    src = re.sub(r"^from \.\w* import .*\n", "", src, flags=re.M)
    src = re.sub(r"^from \. import .*\n", "", src, flags=re.M)
    return src


def chunks(name):
    """Split a module on '# ------ title' marker lines -> {title: code}."""
    out, title, buf = {}, "header", []
    for line in module(name).splitlines():
        m = re.match(r"^# -{5,} (.*)$", line)
        if m:
            out[title] = "\n".join(buf).strip()
            title, buf = m.group(1).strip(), [line]
        else:
            buf.append(line)
    out[title] = "\n".join(buf).strip()
    return out


P = chunks("pipeline")
V = chunks("providers")

# =============================================================== 1. Title
md("""
# AI Product Description Generator
### A practical workflow for creating clearer, consistent product listings

**Abhishek Tiwari - 1338220 · Biren Gohel - 1317182**

This notebook implements every part of the research presentation, slide by slide, and extends it to four generation modes:

| Mode | Input | Output |
|---|---|---|
| **Text → Text** | product facts | title, description, bullets, meta description, review flags |
| **Text → Image** | product facts + style | product visual |
| **Image → Text** | product photo (+ optional facts) | reviewable listing draft |
| **Image → Image** | product photo + style | restyled storefront image |

**Contents (mapped to the slides)**

| § | Slide | Topic |
|---|---|---|
| 0 | – | Setup, API keys and settings |
| 1 | 2 | The design choice: generate a draft from seller facts |
| 2 | 3 | The catalog writing problem |
| 3 | 4 | Why this project was selected |
| 4 | 5 | End-to-end workflow |
| 5 | 6 | Inputs and outputs (data model) |
| 6 | 7 | System architecture |
| 7 | 8 | What happens during one request: validate → normalize |
| 8 | 9 | Prompt and output methodology: build prompt → check response |
| 9 | 15 | Guardrail: unsupported-claim checker |
| 10 | 10 | Interface & implementation: images, provider adapters, exports |
| 11 | 12 | The four modes in action (incl. the slide-12 example) |
| 12 | 7/10 | MongoDB storage |
| 13 | 13 | Evaluation |
| 14 | 14 | Source claims vs. evidence |
| 15 | 15 | Limitations and safeguards (demonstrated) |
| 16 | 11 | Five-day build plan |
| 17 | 16 | Next steps: batch generation + conclusion |
| 18 | 17 | References |
| 19 | – | Using the running web app's REST API |

> Runs **fully offline** with the built-in template provider. Add an `OPENAI_API_KEY` or `GEMINI_API_KEY` to use a real model.
""")

# =============================================================== 0. Setup
md("""
## 0 · Setup

Install the dependencies once (uncomment if needed). MongoDB is optional here: if no server is running at `MONGO_URI`, an in-memory `mongomock` database is used automatically.
""")
code("""
# %pip install -q pydantic httpx pillow pymongo mongomock pandas matplotlib python-dotenv
""")
md("""
**API keys stay out of source control (slide 7).** Put them in a `.env` file next to the project or set environment variables.
Leave them empty to use the offline provider.
""")
code("""
import os
# Optional: paste keys here for this session only - never commit them.
# from getpass import getpass
# os.environ["OPENAI_API_KEY"] = getpass("OpenAI key (blank to skip): ")
# os.environ["GEMINI_API_KEY"] = getpass("Gemini key (blank to skip): ")

import base64, collections, csv, html, io, json, math, re, time, types, datetime as dt
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx
import pandas as pd
import matplotlib.pyplot as plt
from PIL import Image, ImageDraw
from IPython.display import display, Markdown, Image as IPImage
from pydantic import BaseModel, Field, ValidationError

pd.set_option("display.max_colwidth", 120)
plt.rcParams.update({"figure.dpi": 110, "axes.spines.top": False, "axes.spines.right": False})
""")
code(module("config") + """

print("Provider used for 'auto':", settings.resolve_provider(None))
print("Configured providers   :", settings.available_providers())
""")

# =============================================================== 1. Design choice
md("""
## 1 · The design choice (slide 2)

> **Generate a draft from facts the seller provides.**

* A form collects a product name, category, features, audience, tone and an optional search phrase.
* Python checks the entries, builds instructions and a response schema, then calls the selected language model.
* The app returns separate fields - title, description, bullets and suggested metadata - so a seller can edit and export them.

The first version sends the facts with each request. It does **not** train a model or search a catalog database.
""")

# =============================================================== 2. Problem
md("## 2 · The catalog writing problem (slide 3)\nA product listing has to be both informative and easy to scan.")
code("""
problem = pd.DataFrame({
    "What sellers have": ["Raw attributes and notes", "Different writing styles across SKUs",
                          "Repeated manual drafting", "Search terms may be overlooked"],
    "What customers need": ["A quick sense of fit and use", "Consistent voice across a range",
                            "Fast updates when products change", "Clear titles and descriptions"],
})
display(problem)
display(Markdown("**Human cost:** a wrong material, size, compatibility statement, or performance claim can mislead a buyer. "
                 "*Automation should remove blank-page work, not remove responsibility.*"))
""")

# =============================================================== 3. Selection
md("## 3 · Why this project was selected (slide 4)\nThe original deck compared four ideas on build effort and infrastructure.")
code("""
ideas = pd.DataFrame([
    ["Product description generator", "Very low", "LLM API", "3–5 days", 0.6],
    ["Healthcare chatbot with RAG", "High", "Embeddings + vector DB", "3–4 weeks", 3.5],
    ["3D model generator", "Very high", "GPU + custom models", "8–12 weeks", 10],
    ["Autonomous code agent", "High", "Sandbox + agent tools", "4–6 weeks", 5],
], columns=["Project idea", "Effort", "Main infrastructure", "Source estimate", "Midpoint (weeks)"])
display(ideas)

fig, ax = plt.subplots(figsize=(7, 2.6))
colors = ["#6d4aff" if i == 0 else "#c9cbe0" for i in range(len(ideas))]
ax.barh(ideas["Project idea"], ideas["Midpoint (weeks)"], color=colors, height=0.55)
for y, v in enumerate(ideas["Midpoint (weeks)"]):
    ax.text(v + 0.15, y, f"{v:g} wk", va="center", fontsize=9, color="#4b4d6b")
ax.invert_yaxis(); ax.set_xlabel("Estimated build time, weeks (midpoint of source estimate)")
ax.set_title("Planning estimates from the source presentation", loc="left", fontsize=11)
plt.tight_layout(); plt.show()
""")
md("The generator ranked well because its first version needs a narrow input-to-output flow and a model API, rather than custom training or specialized compute.")

# =============================================================== 4. Workflow
md("""
## 4 · End-to-end workflow (slide 5)

The seller stays in control at every point where facts or wording can change.

```
 1 Enter facts  ─►  2 Choose style  ─►  3 Generate  ─►  4 Review  ─►  5 Export
 name, category,    brand voice,         prompt = facts    compare every    copy or export
 materials,         keyword,             + rules + schema  claim with       plain / HTML /
 features,          destination format   + model request   product data     JSON / CSV
 audience
```

A generated draft is a **starting point**. A person checks accuracy, edits the copy, then approves the final listing.
""")

# =============================================================== 5. Data model
md("## 5 · Inputs and outputs (slide 6)\nThe data model. Only name and category are required - optional fields stay optional and missing facts are never guessed.")
code(module("schemas"))
code("""
io_table = pd.DataFrame([
    ["Product name + category", "Names the item and sets the product context", "Title and short heading"],
    ["Materials, size, features", "Provides facts the copy can safely use", "Description and feature bullets"],
    ["Target audience / use", "Helps explain relevance without inventing specs", "Use-focused benefit framing"],
    ["Brand voice", "Guides style while facts remain unchanged", "Professional, playful, luxury, or minimal copy"],
    ["Target keyword", "Optional phrase to include naturally", "Suggested title / meta description"],
    ["Output format", "Matches the seller's next step", "Plain text, HTML, JSON, or CSV"],
], columns=["Input from seller", "Why it matters", "Possible generated output"])
display(io_table)
print("Tones:", TONES); print("Formats:", OUTPUT_FORMATS); print("Image styles:", list(IMAGE_STYLES))
""")

# =============================================================== 6. Architecture
md("""
## 6 · System architecture (slide 7)

A thin application layer coordinates the request and formats the answer.

```
Seller ──► Web UI ──► FastAPI logic ──► LLM provider ──► Response check ──► MongoDB ──► Seller
facts,     form        validate,          OpenAI /         parse JSON,         history,     edit, approve,
style,     validation, normalize,         Gemini /         required fields,    images,      copy or export
format     preview     prompt, adapter    offline          claim guardrail     logs
```

**Server-side responsibilities:** keep provider credentials out of the browser and source control; own timeouts, safe errors,
request limits, and the minimal logs needed to diagnose failures. The deck proposed Streamlit for the UI; this build uses a
FastAPI backend + custom web frontend (see `backend/` and `frontend/`) so all four modes share one API, and this notebook for the research walkthrough.
""")

# =============================================================== 7. Validate + normalize
md("""
## 7 · What happens during one request (slide 8)

| 01 Validate | 02 Normalize | 03 Build prompt | 04 Call model | 05 Check response | 06 Return draft |
|---|---|---|---|---|---|
| required fields, length, tone, export option | trim whitespace, package facts | facts + tone rules + keyword + JSON schema | timeout + bounded retry | parse JSON, field types, empty results | field-by-field preview, copy/export |

If a request times out or the response fails validation, explain the issue, keep the form intact, and let the seller retry.
""")
code(P["header"])
code(P["01 validate"])
code("""
# Validation demo: missing required fields + an unknown tone
try:
    validate_facts(ProductFacts(product_name="", category="", brand_voice="sarcastic"))
except InputProblem as e:
    display(pd.Series(e.errors, name="message").to_frame())
""")
code(P["02 normalize"])
code("""
raw = ProductFacts(product_name="  Reusable   insulated bottle ", category=" Drinkware",
                   features=["- Double-wall construction\\n• double-wall construction", " Leak-resistant lid ;  "],
                   brand_voice="Professional")
clean = normalize_facts(raw)
print(clean.model_dump_json(indent=2))
print()
print(facts_block(clean))
""")

# =============================================================== 8. Prompt + check
md("""
## 8 · Prompt and output methodology (slide 9)

**Constrain the shape of the answer; review its meaning separately.**

Instruction layer:
* Treat user-provided attributes as the source of truth.
* Write for the requested audience and selected brand voice.
* Use the keyword naturally; never repeat it just to hit a count.
* Omit unsupported claims and flag missing information.

Response shape: `{"title", "description", "bullets": [...], "meta_description", "review_flags": [...]}`
""")
code(P["03 build prompt"])
code("""
example_facts = normalize_facts(ProductFacts(
    product_name="Reusable insulated bottle", category="Drinkware", materials="Stainless steel",
    features=["Double-wall construction"], brand_voice="clear and practical", keyword="insulated bottle"))
prompt = build_prompt(example_facts)
print("SYSTEM:\\n" + prompt["system"]); print("\\nUSER:\\n" + prompt["user"])
print("\\nJSON schema sent to the model:"); print(json.dumps(LISTING_JSON_SCHEMA, indent=1))
""")
code(P["05 check response"])
code("""
# Schema validation catches broken *structure*...
for bad in ['Sure! Here is your listing: {"title": "x"}', "not json at all", '{"title":"T","description":"","bullets":[],"meta_description":"","review_flags":[]}']:
    try:
        check_response(parse_model_json(bad))
    except ResponseProblem as e:
        print("Rejected:", e)

# ...and accepts well-formed output wrapped in code fences.
ok = check_response(parse_model_json('```json\\n{"title":"Bottle","description":"A bottle.","bullets":["- One"],"meta_description":"A bottle.","review_flags":[]}\\n```'))
print("Accepted:", ok)
""")

# =============================================================== 9. Guardrail
md("""
## 9 · Guardrail: unsupported-claim checker (slide 15 safeguard)

*Schema validation checks structure, not factual truth.* So after the structure check we compare the draft against the
supplied facts: any number or risky claim (e.g. "BPA-free", "waterproof", "24 hours") that is not in the facts becomes a
review flag. It is a heuristic that **supports** the human reviewer - it does not replace them.
""")
code(P["guardrail: unsupported claims"])
code("""
hallucinated = ListingDraft(
    title="Insulated bottle - keeps drinks cold 24 hours",
    description="BPA-free stainless steel bottle that keeps drinks cold for 24 hours. Best insulated bottle, insulated bottle lovers love this insulated bottle. 750 ml.",
    bullets=["Leak-proof lid", "Dishwasher safe"], meta_description="Insulated bottle insulated bottle", review_flags=[])
for f in finalize(hallucinated, example_facts).review_flags:
    print("⚠", f)
""")

# =============================================================== 10. Implementation
md("""
## 10 · Interface and implementation details (slide 10)

| Layer | Implementation | Responsibility |
|---|---|---|
| User interface | Web frontend (`frontend/`) | Collect inputs, show preview, expose actions |
| Application logic | Python functions (this notebook / `backend/pipeline.py`) | Validate, compose prompt, call provider |
| Model access | OpenAI or Gemini API (+ offline provider) | Generate candidate fields and images |
| Output handling | JSON schema + parsers + exporters | Check required fields and format exports |
| Operations | Secrets + logs + limits | Protect keys, control retries and usage |

Provider code is kept separate from UI code. First the image utilities used by the offline provider and for validating uploads:
""")
code(module("imaging") + """

# expose as a namespace so provider code can call imaging.load_image(...) exactly like the backend
imaging = types.SimpleNamespace(load_image=load_image, to_png=to_png, analyze=analyze, restyle=restyle,
                                render_concept=render_concept, ImageProblem=ImageProblem)
""")
md("### Provider adapters - step 04 *Call model*\nHTTPS calls with a timeout and a bounded retry policy (retries only on timeouts, HTTP 429 and 5xx). Errors are turned into short, safe messages.")
code(V["header"])
code(V["HTTP helper"])
code(V["base"])
md("**OpenAI** - Structured Outputs (`response_format: json_schema`, strict) for text and vision; `gpt-image-2` for image generation and editing.")
code(V["OpenAI"])
md("**Gemini** - `responseSchema` + `responseMimeType: application/json` for text and vision; `gemini-nano-banana-2.1` (Nano Banana 2.1) for image generation and editing.")
code(V["Gemini"])
md("**Offline provider** - deterministic templates and Pillow image processing. It never invents numbers or claims, which makes it a useful baseline.")
code(V["Mock (offline)"])
md("### Exports - plain text, HTML, JSON, CSV")
code(module("exports"))

# =============================================================== 11. Four modes
md("""
## 11 · The four modes in action (slide 12 + extensions)

These helper functions run the full pipeline for each mode and record latency (used later for evaluation).
""")
code("""
RUN_LOG: List[dict] = []   # notebook copy of request_logs


def _timed(mode, provider_name, fn):
    t0 = time.perf_counter()
    try:
        out = fn(); status, code = "ok", ""
        return out
    except Exception as e:
        status, code = "error", getattr(e, "code", type(e).__name__)
        raise
    finally:
        RUN_LOG.append({"mode": mode, "provider": provider_name, "status": status, "error_code": code,
                        "latency_ms": round((time.perf_counter() - t0) * 1000, 1)})


def text_to_text(facts: ProductFacts, voice: Optional[dict] = None) -> ListingDraft:
    name = settings.resolve_provider(facts.provider)
    def run():
        validate_facts(facts)
        f = normalize_facts(facts)
        provider = get_provider(name, settings)
        raw = provider.generate_listing(build_prompt(f, voice), f)
        return finalize(check_response(parse_model_json(raw)), f)
    return _timed("text-to-text", name, run)


def text_to_image(facts: ProductFacts, style="studio_white", extra="") -> Tuple[bytes, str]:
    name = settings.resolve_provider(facts.provider)
    def run():
        validate_facts(facts)
        f = normalize_facts(facts)
        prompt = build_image_prompt(f, style, extra)
        return to_png(load_image(get_provider(name, settings).generate_image(prompt, f, style), 25)), prompt
    return _timed("text-to-image", name, run)


def image_to_text(image_bytes: bytes, hints: Optional[ProductFacts] = None) -> Tuple[ListingDraft, dict]:
    hints = hints or ProductFacts()
    name = settings.resolve_provider(hints.provider)
    def run():
        png = to_png(load_image(image_bytes, settings.max_image_mb))
        validate_facts(hints, require_name=False)
        f = normalize_facts(hints)
        raw = get_provider(name, settings).describe_image(build_vision_prompt(f), png, f)
        return finalize(check_response(parse_model_json(raw)), f), analyze(load_image(png))
    return _timed("image-to-text", name, run)


def image_to_image(image_bytes: bytes, style="studio_white", extra="", provider="auto") -> bytes:
    name = settings.resolve_provider(provider)
    def run():
        png = to_png(load_image(image_bytes, settings.max_image_mb))
        return to_png(load_image(get_provider(name, settings).edit_image(png, build_edit_prompt(style, extra), style), 25))
    return _timed("image-to-image", name, run)


def show_draft(d: ListingDraft):
    flags = "\\n".join(f"- ⚠ {x}" for x in d.review_flags) or "- none"
    bullets = "\\n".join(f"- {b}" for b in d.bullets)
    display(Markdown(f"### {d.title}\\n{d.description}\\n\\n{bullets}\\n\\n**Meta:** {d.meta_description}\\n\\n**Review flags**\\n{flags}"))
""")
md("""
### Mode 1 · Text → Text - the slide-12 example
*Illustrative example only. These product details are invented for demonstration.*

| Field | Value |
|---|---|
| Product | Reusable insulated bottle |
| Material | Stainless steel |
| Feature | Double-wall construction |
| Tone / keyword | Clear and practical / insulated bottle |
""")
code("""
bottle = ProductFacts(product_name="Reusable insulated bottle", category="Drinkware", materials="Stainless steel",
                      features=["Double-wall construction"], brand_voice="clear and practical",
                      keyword="insulated bottle")
draft = text_to_text(bottle)
show_draft(draft)
""")
md("Expected from the slide: *“Capacity and heat-retention duration were not supplied. Do not add either claim.”* - the size flag above is exactly that guardrail. Same facts in every brand voice:")
code("""
rows = []
for tone in TONES:
    d = text_to_text(bottle.model_copy(update={"brand_voice": tone}))
    rows.append({"tone": tone, "title": d.title, "description": d.description})
display(pd.DataFrame(rows))
""")
md("### Mode 2 · Text → Image")
code("""
png, img_prompt = text_to_image(bottle, style="luxury_dark")
print("Prompt:", img_prompt)
display(IPImage(png, width=360))
print("Review flag: generated images are illustrations - compare against the real product before publishing.")
""")
md("### Mode 3 · Image → Text\nWe draw a simple synthetic product photo so the notebook has no external files. Replace `photo_bytes` with `open('your_photo.jpg','rb').read()` to try your own.")
code("""
photo = Image.new("RGB", (800, 800), (244, 244, 246))
d = ImageDraw.Draw(photo)
d.rounded_rectangle([310, 190, 490, 690], 60, fill=(32, 92, 168))
d.rectangle([340, 120, 460, 195], fill=(40, 40, 40))
buf = io.BytesIO(); photo.save(buf, "JPEG", quality=92); photo_bytes = buf.getvalue()
display(IPImage(photo_bytes, width=220))

draft_img, visual = image_to_text(photo_bytes, ProductFacts(category="Drinkware", keyword="water bottle"))
print("Visual analysis:", visual)
show_draft(draft_img)
""")
md("### Mode 4 · Image → Image - every style")
code("""
fig, axes = plt.subplots(1, len(IMAGE_STYLES) + 1, figsize=(15, 3.2))
axes[0].imshow(photo); axes[0].set_title("original", fontsize=9)
for ax, style in zip(axes[1:], IMAGE_STYLES):
    out = image_to_image(photo_bytes, style)
    ax.imshow(Image.open(io.BytesIO(out))); ax.set_title(style, fontsize=9)
for ax in axes: ax.axis("off")
plt.tight_layout(); plt.show()
""")
md("### Export - matches the seller's next step")
code("""
for fmt in OUTPUT_FORMATS:
    content, mime, ext = export_draft(draft, fmt, bottle.product_name)
    print(f"──── {fmt.upper()}  ({mime}, .{ext})"); print(content)
""")

# =============================================================== 12. MongoDB
md("""
## 12 · MongoDB storage

| Collection | Contents |
|---|---|
| `generations` | mode, provider, model, inputs, output, image ids, human review |
| `images` | uploaded and generated PNGs (BSON Binary) |
| `voices` | saved brand voices |
| `request_logs` | mode, provider, latency, status - **no product data** |

Start MongoDB locally (`brew services start mongodb-community` or `docker run -p 27017:27017 mongo`) or point `MONGO_URI`
at MongoDB Atlas. Without a server, the in-memory fallback is used.
""")
code(module("db"))
code("""
store = Store(settings)
print("Database backend:", store.backend)

# save the slide-12 run, the images and a brand voice
gid = store.save_generation({"mode": "text-to-text", "provider": settings.resolve_provider(None), "model": "notebook",
                             "input": normalize_facts(bottle).model_dump(), "output": draft.model_dump(), "image_ids": []})
img_id = store.save_image(png, "generated")
store.save_generation({"mode": "text-to-image", "provider": settings.resolve_provider(None), "model": "notebook",
                       "input": bottle.model_dump(), "output": {"image_id": img_id, "prompt": img_prompt}, "image_ids": [img_id]})
vid = store.save_voice({"name": "Nordic calm", "base_tone": "minimal",
                        "rules": "Short sentences. No exclamation marks. Mention care last.", "example": ""})
for r in RUN_LOG:
    store.log_request(r["mode"], r["provider"], r["latency_ms"], r["status"], r["error_code"])

print({c: store.db[c].count_documents({}) for c in ["generations", "images", "voices", "request_logs"]})
pd.DataFrame(store.list_generations())[["id", "mode", "provider", "created_at"]]
""")
code("""
# A saved voice changes style, never facts
voice = store.get_voice(vid)
show_draft(text_to_text(bottle.model_copy(update={"brand_voice": "minimal"}), voice=voice))
print(build_prompt(normalize_facts(bottle), voice)["user"].split("Target keyword")[0][-260:])
""")

# =============================================================== 13. Evaluation
md("""
## 13 · Evaluation: measure usefulness before claiming impact (slide 13)

| Question | Measure | How to evaluate |
|---|---|---|
| Did it save time? | Seconds per approved listing | Compare manual and assisted tasks on the same product set |
| Are facts accurate? | Unsupported claim rate | Review each generated claim against source product data |
| Is the copy usable? | Human quality rating | Blind reviewer scores clarity, completeness, voice |
| Does the interface work? | Task completion / error rate | Observe sellers completing input, review, export |
| Does it help search? | Search performance over time | Test live pages; separate copy from technical SEO changes |

Below we run a small **illustrative** test set (invented products) through the pipeline and compute the automatic measures.
Human measures (rating, unsupported claims, time to approve) must come from real reviewers - a template is provided.
""")
code("""
test_set = [
    ProductFacts(product_name="Reusable insulated bottle", category="Drinkware", materials="Stainless steel",
                 features=["Double-wall construction"], brand_voice="clear and practical", keyword="insulated bottle"),
    ProductFacts(product_name="Bamboo cutting board", category="Home & Kitchen", materials="Bamboo", size="45 x 30 cm",
                 features=["Juice groove", "Hanging hole"], brand_voice="professional", keyword="bamboo chopping board"),
    ProductFacts(product_name="Wireless earbuds", category="Electronics", features=["Bluetooth 5.3", "Charging case", "Touch controls"],
                 target_audience="commuters", brand_voice="playful", keyword="wireless earbuds"),
    ProductFacts(product_name="Linen cushion cover", category="Home decor", materials="Linen", size="45 x 45 cm",
                 features=["Hidden zip"], brand_voice="luxury", keyword="linen cushion cover"),
    ProductFacts(product_name="Yoga mat", category="Sports & Outdoors", materials="TPE", size="183 x 61 cm, 6 mm thick",
                 features=["Textured non-slip surface", "Carry strap"], brand_voice="minimal"),
    ProductFacts(product_name="Stoneware coffee mug", category="Drinkware", materials="Stoneware", size="350 ml",
                 features=["Reactive glaze", "Wide handle"], brand_voice="luxury", keyword="handmade mug"),
    ProductFacts(product_name="Kids rain jacket", category="Apparel", materials="Polyester",
                 features=["Hood", "Reflective strips"], target_audience="children aged 4-8", brand_voice="playful"),
    ProductFacts(product_name="LED desk lamp", category="Lighting", materials="Aluminium",
                 features=["Adjustable arm", "USB-C power", "Three brightness levels"], brand_voice="minimal", keyword="desk lamp"),
]

rows = []
for f in test_set:
    t0 = time.perf_counter()
    d = text_to_text(f)
    ms = (time.perf_counter() - t0) * 1000
    text = " ".join([d.title, d.description, *d.bullets]).lower()
    rows.append({
        "product": f.product_name, "tone": f.brand_voice, "latency_ms": round(ms, 1),
        "review_flags": len(d.review_flags),
        "unsupported_auto": sum("Unsupported" in x or "not supported" in x for x in d.review_flags),
        "keyword_uses": text.count(f.keyword.lower()) if f.keyword else None,
        "title_len": len(d.title), "meta_len": len(d.meta_description), "bullets": len(d.bullets),
    })
results = pd.DataFrame(rows)
display(results)
print(f"Median latency {results.latency_ms.median():.1f} ms · auto-detected unsupported claims: {results.unsupported_auto.sum()} · "
      f"meta descriptions within 160 chars: {(results.meta_len <= 160).mean():.0%}")
""")
code("""
fig, ax = plt.subplots(figsize=(7, 3))
ax.barh(results["product"], results["review_flags"], color="#6d4aff", height=0.55)
ax.invert_yaxis(); ax.set_xlabel("Review flags raised (missing facts, unsupported claims, keyword use)")
ax.set_title("Guardrail flags per product - illustrative test set", loc="left", fontsize=11)
ax.xaxis.get_major_locator().set_params(integer=True)
plt.tight_layout(); plt.show()
""")
md("**Human review template** - fill this with real reviewer data (blind scoring). The numbers are *not* pre-filled on purpose: inventing them would contradict slide 14.")
code("""
review_template = pd.DataFrame({
    "product": results["product"],
    "manual_seconds": pd.NA,           # time to write the listing by hand
    "assisted_seconds": pd.NA,         # time from draft shown to approval (the web app records this)
    "claims_checked": pd.NA,
    "unsupported_claims": pd.NA,
    "rating_1_to_5": pd.NA,            # clarity, completeness, voice
})
review_template.to_csv("human_review_template.csv", index=False)
display(review_template.head(3))


def summarise_review(df: pd.DataFrame) -> dict:
    df = df.dropna()
    if df.empty:
        return {"status": "no reviewer data yet"}
    return {
        "time_saving_%": round(100 * (1 - df.assisted_seconds.sum() / df.manual_seconds.sum()), 1),
        "speed_up_x": round(df.manual_seconds.sum() / df.assisted_seconds.sum(), 1),
        "unsupported_claim_rate": round(df.unsupported_claims.sum() / df.claims_checked.sum(), 3),
        "mean_rating": round(df.rating_1_to_5.mean(), 2), "n": len(df),
    }

print(summarise_review(review_template))
print("Metrics from MongoDB:", {k: v for k, v in store.metrics().items() if k != "by_mode"})
""")

# =============================================================== 14. Claims vs evidence
md("""
## 14 · What the source claims, and what the evidence supports (slide 14)

| Source claim | What we can say now | What to verify |
|---|---|---|
| “Under 3 seconds” generation | Unmeasured response-time target | Record latency across models and request sizes |
| 30 min → under 30 sec; “95%” reduction | Source estimates conflict mathematically | Time matched tasks; clarify what is included |
| “10× faster” catalog updates | No workflow benchmark supplied | Measure batch size and approval time |
| “High-converting” copy | No conversion study supplied | Run controlled experiment and report sample size |
""")
code("""
manual_s, assisted_s = 30 * 60, 30
print(f"30 min -> 30 s is {manual_s / assisted_s:.0f}x faster, a {100 * (1 - assisted_s / manual_s):.1f}% reduction (not 95%).")
print(f"A 95% reduction from 30 min would leave {manual_s * 0.05 / 60:.1f} min, i.e. {manual_s * 0.05:.0f} s - not 30 s.")

ok = [r["latency_ms"] for r in RUN_LOG if r["status"] == "ok"]
print(f"\\nMeasured in this notebook ({settings.resolve_provider(None)} provider): {len(ok)} requests, "
      f"median {pd.Series(ok).median():.1f} ms, {(pd.Series(ok) < 3000).mean():.0%} under 3 s.")
print("Note: offline-template latency says nothing about a real LLM. Re-run with an API key and report model, request size and sample size.")
""")

# =============================================================== 15. Limitations
md("""
## 15 · Limitations and safeguards (slide 15) - demonstrated

| Limitation | Practical safeguard | Where in this project |
|---|---|---|
| Model may invent or overstate a benefit | Generate from supplied facts; flag and review claims | fact-only prompt, `find_unsupported_claims`, review gate |
| SEO wording cannot guarantee rank or traffic | Accurate content, measure real outcomes | `keyword_flags`; evaluation §13 |
| Provider latency, outages, or cost can vary | Timeouts, retries with limits, usage monitoring | `_post`, rate limiter, `request_logs` |
| Tone may sound generic or inconsistent | Brand examples, saved voice rules, human editing | `voices` collection |
| Export may not match every storefront schema | Test destination formats, validate fields | `export_draft` |
| Product data can be sensitive or proprietary | Minimise retained data, access controls | `STORE_INPUTS`, delete endpoint, logs without product data |
| **Generated images are illustrations** | Flag every image; compare to the real item | Text → Image flag |
| **Image → Text can misread photos** | Only visible details; material/size “not verified” | vision prompt + flags |
| **Image → Image may alter the product** | Prompt locks shape/colour/labels; reviewer confirms | edit prompt + review checkbox |
""")
md("**a) Invented benefits are flagged** (shown in §9). **b) Missing facts are never guessed:**")
code("""
sparse = text_to_text(ProductFacts(product_name="Canvas tote bag", category="Bags"))
show_draft(sparse)
""")
md("**c) Provider timeouts → bounded retries and a safe message.** We simulate a provider that always times out:")
code("""
from unittest import mock

calls = {"n": 0}
def always_timeout(*a, **k):
    calls["n"] += 1
    raise httpx.TimeoutException("simulated")

fast = Settings(); fast.max_retries = 2
with mock.patch.object(httpx.Client, "post", always_timeout), mock.patch.object(time, "sleep", lambda s: None):
    try:
        _post(fast, "https://example.invalid", headers={}, json_body={})
    except ProviderError as e:
        print(f"Attempts: {calls['n']} (1 + {fast.max_retries} retries) -> code={e.code!r}, message={e.message!r}")
""")
md("**d) Schema validation checks structure, not truth** - this passes the schema yet contains a false claim; only the guardrail and the human reviewer catch it:")
code("""
looks_fine = check_response({"title": "Insulated bottle", "description": "Keeps drinks hot for 12 hours.",
                             "bullets": ["Double-wall construction"], "meta_description": "Bottle", "review_flags": []})
print("Schema check passed:", isinstance(looks_fine, ListingDraft))
print("Guardrail:", finalize(looks_fine, example_facts).review_flags)
""")
md("**e) Bad uploads are rejected safely:**")
code("""
for label, data in [("text file", b"hello"), ("empty", b""), ("too large", b"0" * int(9 * 1024 * 1024))]:
    try:
        load_image(data, max_mb=8)
    except ImageProblem as e:
        print(f"{label:10s} -> {e}")
""")
md("**f) Data minimisation:** deleting a generation also deletes its images.")
code("""
before = store.images.count_documents({})
gens = store.list_generations(mode="text-to-image")
store.delete_generation(gens[0]["id"])
print("images before/after delete:", before, "->", store.images.count_documents({}))
""")

# =============================================================== 16. Build plan
md("""
## 16 · Five-day build plan (slide 11)

| Day | Goal | Delivered in this project |
|---|---|---|
| Day 1 | Working form - fields, test API request, one basic result | `frontend/`, `/api/generate/text-to-text` |
| Day 2 | Prompt design - output fields, tone rules, examples exposing unsupported claims | §8, §9 |
| Day 3 | SEO + formatting - keyword guidance, export formats | `keyword_flags`, `export_draft` |
| Days 4–5 | Review + export - representative products, error tests, copy/export, polish | §13, §15, review gate in the UI, `tests/` |

Source estimate: 3–5 calendar days, about 15 development hours. Team size and delivery scope were not specified.
""")

# =============================================================== 17. Next steps
md("""
## 17 · Next steps and conclusion (slide 16)

1. Build the smallest usable flow with fact-first prompts and a reviewable schema. ✅
2. Test output quality and time savings on a representative catalogue sample. → use §13 with real reviewers.
3. Add saved brand voices ✅, batch editing (prototype below) and direct store integrations only after validation.
""")
code("""
# Batch prototype: catalogue CSV in -> drafts CSV out (every row still needs human review)
catalogue = pd.DataFrame([f.model_dump() for f in test_set])
catalogue["features"] = catalogue["features"].apply(lambda x: "; ".join(x))
catalogue.to_csv("sample_catalogue.csv", index=False)

def batch_generate(path_in: str, path_out: str) -> pd.DataFrame:
    out = []
    for row in pd.read_csv(path_in).fillna("").to_dict("records"):
        row["features"] = [x.strip() for x in str(row["features"]).split(";") if x.strip()]
        row["brand_voice_id"] = None
        try:
            d = text_to_text(ProductFacts(**row))
            out.append({"product_name": row["product_name"], **d.model_dump(), "status": "needs review"})
        except (InputProblem, ProviderError, ResponseProblem) as e:
            out.append({"product_name": row["product_name"], "status": f"error: {e}"})
    df = pd.DataFrame(out); df.to_csv(path_out, index=False)
    return df

batch_generate("sample_catalogue.csv", "sample_drafts.csv")[["product_name", "title", "status"]]
""")
md("""
> ### Conclusion
> **Automate the repetitive draft. Keep product truth and publication decisions with the seller.**
""")

# =============================================================== 18. References
md("""
## 18 · References (slide 17)

1. OpenAI API, Structured Outputs - https://platform.openai.com/docs/guides/structured-outputs
2. Google AI for Developers, Gemini structured outputs - https://ai.google.dev/gemini-api/docs/generate-content/structured-output
3. Streamlit documentation, Secrets management - https://docs.streamlit.io/develop/concepts/connections/secrets-management
4. Google Search Central, Developer guide - https://developers.google.com/search/docs/fundamentals/get-started-developers
5. Google Search Central, Share product data with Google - https://developers.google.com/search/docs/specialty/ecommerce/share-your-product-data-with-google
""")

# =============================================================== 19. REST API
md("""
## 19 · Using the running web app's REST API

Start the backend from the project root: `uvicorn backend.main:app --port 8000`, open http://localhost:8000, then run this cell.
""")
code("""
API = "http://localhost:8000"
try:
    print(httpx.get(f"{API}/api/health", timeout=3).json())
    r = httpx.post(f"{API}/api/generate/text-to-text", json=bottle.model_dump(), timeout=60).json()
    print(json.dumps(r["draft"], indent=2))
    r = httpx.post(f"{API}/api/generate/image-to-image", files={"image": ("p.jpg", photo_bytes, "image/jpeg")},
                   data={"style": "studio_white"}, timeout=120).json()
    display(IPImage(httpx.get(API + r["image_url"]).content, width=240))
except httpx.HTTPError:
    print("Backend not running - start it with: uvicorn backend.main:app --port 8000")
""")

nb = nbf.v4.new_notebook()
nb["cells"] = cells
nb["metadata"] = {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                  "language_info": {"name": "python"}}
nbf.write(nb, OUT)
print(f"Wrote {OUT} ({len(cells)} cells)")
