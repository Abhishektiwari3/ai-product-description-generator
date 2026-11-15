# ListingLab AI — AI Product Description Generator

*A practical workflow for creating clearer, consistent product listings.*
Abhishek Tiwari (1338220) · Biren Gohel (1317182)

Built from the research presentation: fact-first generation, a reviewable JSON schema, a human review step,
and evaluation that measures usefulness before claiming impact. Extended to **four modes**:

| Mode | Input → Output | Endpoint |
|---|---|---|
| Text → Text | product facts → title, description, bullets, meta description, review flags | `POST /api/generate/text-to-text` |
| Text → Image | product facts + style → product visual | `POST /api/generate/text-to-image` |
| Image → Text | product photo (+ optional facts) → listing draft | `POST /api/generate/image-to-text` |
| Image → Image | product photo + style → restyled storefront image | `POST /api/generate/image-to-image` |

## Project layout

```
backend/            FastAPI app (Python)
  config.py         settings from .env (keys stay server-side)
  schemas.py        input/output data model (slide 6, 9)
  pipeline.py       validate → normalize → build prompt → check response → claim guardrail (slide 8, 9)
  providers.py      OpenAI, Gemini and offline adapters; timeout + bounded retry (slide 7, 10)
  imaging.py        image validation, offline image generation/restyling (Pillow)
  exports.py        plain text / HTML / JSON / CSV (slide 6)
  db.py             MongoDB: generations, images, voices, request_logs + evaluation metrics (slide 13)
  main.py           REST API + serves the frontend
frontend/           single-page web app (HTML/CSS/JS, no build step)
notebook/           AI_Product_Description_Generator.ipynb — slide-by-slide research notebook
tests/              API tests (offline)
```

## Quick start (any device)

Needs **Python 3.9+** and **git**.

**macOS / Linux**

```bash
git clone https://github.com/Abhishektiwari3/ai-product-description-generator.git
cd ai-product-description-generator
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # optional: add OPENAI_API_KEY or GEMINI_API_KEY
uvicorn backend.main:app --reload --port 8000
```

**Windows (PowerShell)**

```powershell
git clone https://github.com/Abhishektiwari3/ai-product-description-generator.git
cd ai-product-description-generator
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env        # optional: add OPENAI_API_KEY or GEMINI_API_KEY
uvicorn backend.main:app --reload --port 8000
```

Open **http://localhost:8000**.

Next time on the same device you only need to `cd` into the folder, activate `.venv` and run the `uvicorn` line.
To get the latest changes on a device you already cloned to, run `git pull`.

> `.env` holds your API keys and is never uploaded (it is in `.gitignore`). Create it again on each device.

* **No API key?** Everything still works with the built-in *offline* provider (template copy + Pillow image processing). The header shows “Offline AI”.
* **No MongoDB?** The app falls back to an in-memory database (header shows “In-memory DB”; data is lost on restart).

### MongoDB

```bash
# macOS (Homebrew)
brew tap mongodb/brew && brew install mongodb-community && brew services start mongodb-community
# or Docker
docker run -d -p 27017:27017 --name mongo mongo:7
```

Or set `MONGO_URI` to a MongoDB Atlas connection string. Collections: `generations`, `images`, `voices`, `request_logs`.

### Notebook

```bash
jupyter notebook notebook/AI_Product_Description_Generator.ipynb
```

The notebook is self-contained and runs offline. Its code cells are generated from `backend/` by
`notebook/build_notebook.py`, so the notebook and the app stay identical.

### Tests

```bash
pytest -q
```

## Coverage of the presentation

| Slide | Where |
|---|---|
| 2 Design choice | Studio form → structured draft; notebook §1 |
| 3 Problem · 4 Selection | About page; notebook §2–3 |
| 5 Workflow | 5-step stepper in Studio (Enter facts → Choose style → Generate → Review → Export) |
| 6 Inputs/outputs | `schemas.py`, form fields, 4 export formats |
| 7 Architecture | FastAPI backend, keys in `.env`, rate limit, safe errors, minimal logs |
| 8 One request | `pipeline.py` steps 01–06, retry in `providers._post`, form kept on error |
| 9 Prompt methodology | `SYSTEM_RULES`, JSON schema, Structured Outputs / responseSchema |
| 10 Implementation | provider code separate from UI; “review before publishing” gate |
| 11 Build plan | About page; notebook §16 |
| 12 Example | “Load example” button; notebook §11 |
| 13 Evaluation | Evaluation page (latency, approval rate, rating, seconds per approved listing, unsupported-claim rate) |
| 14 Claims vs evidence | Evaluation page with measured values; 60× / 98.3% correction |
| 15 Limitations | About page table + notebook §15 demonstrations; unsupported-claim guardrail |
| 16 Next steps | Saved brand voices; batch CSV prototype (notebook §17) |
| 17 References | About page; notebook §18 |

## Limitations

* The offline provider is template-based — useful for demos and as a baseline, not model-quality copy.
* The unsupported-claim checker is a heuristic (numbers + risky phrases); it supports, not replaces, human review.
* Schema validation checks structure, not truth. Every draft must be reviewed before publishing.
* Generated images are illustrations; Image → Image can alter product details; Image → Text can misread photos. All are flagged in the UI.
* Search ranking and conversion impact are **not** measured by this app — they need live experiments.
* Latency and cost vary by provider; offline timings say nothing about a real LLM.
