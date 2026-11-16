from __future__ import annotations

import csv
import html
import io
import json
from typing import Tuple

from .schemas import ListingDraft


def export_draft(draft: ListingDraft, fmt: str, product_name: str = "") -> Tuple[str, str, str]:
    """Return (content, mime_type, file_extension)."""
    fmt = (fmt or "plain").lower()
    if fmt == "json":
        body = draft.model_dump()
        body.pop("review_flags", None)
        return json.dumps(body, indent=2, ensure_ascii=False), "application/json", "json"
    if fmt == "html":
        bullets = "\n".join(f"    <li>{html.escape(b)}</li>" for b in draft.bullets)
        content = (
            f'<meta name="description" content="{html.escape(draft.meta_description)}">\n'
            f"<article class=\"product-listing\">\n"
            f"  <h1>{html.escape(draft.title)}</h1>\n"
            f"  <p>{html.escape(draft.description)}</p>\n"
            f"  <ul>\n{bullets}\n  </ul>\n"
            f"</article>\n"
        )
        return content, "text/html", "html"
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["product_name", "title", "description", "bullets", "meta_description"])
        w.writerow([product_name, draft.title, draft.description, " | ".join(draft.bullets), draft.meta_description])
        return buf.getvalue(), "text/csv", "csv"
    lines = [draft.title, "", draft.description, ""] + [f"• {b}" for b in draft.bullets] + ["", f"Meta: {draft.meta_description}"]
    return "\n".join(lines), "text/plain", "txt"
