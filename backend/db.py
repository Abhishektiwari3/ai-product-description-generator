from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Dict, List, Optional

from bson import Binary, ObjectId
from pymongo import DESCENDING, MongoClient
from pymongo.errors import PyMongoError

from .config import Settings

logger = logging.getLogger("uvicorn.error")
COLLECTIONS = ["generations", "images", "voices", "request_logs"]


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Store:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.backend = "mongodb"
        try:
            client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=1500)
            client.admin.command("ping")
            self.client = client
        except PyMongoError:
            if not settings.allow_memory_fallback:
                raise
            import mongomock

            self.client = mongomock.MongoClient()
            self.backend = "memory (mongomock fallback)"
            logger.warning("MongoDB not reachable at %s - using an in-memory database. Data is lost on restart.",
                           settings.mongo_uri)
        self.db = self.client[settings.mongo_db]
        self._create_collections()
        self.generations = self.db["generations"]
        self.images = self.db["images"]
        self.voices = self.db["voices"]
        self.logs = self.db["request_logs"]
        self._indexes()
        logger.info("Database '%s' ready (%s).", settings.mongo_db, self.backend)

    def _create_collections(self) -> None:
        existing = set(self.db.list_collection_names())
        for name in COLLECTIONS:
            if name not in existing:
                self.db.create_collection(name)

    def _indexes(self) -> None:
        self.generations.create_index([("created_at", DESCENDING)])
        self.generations.create_index("mode")
        self.logs.create_index([("created_at", DESCENDING)])
        self.voices.create_index("name", unique=True)

    def save_image(self, png: bytes, kind: str) -> str:
        res = self.images.insert_one({"data": Binary(png), "mime": "image/png", "kind": kind,
                                      "size": len(png), "created_at": now()})
        return str(res.inserted_id)

    def get_image(self, image_id: str) -> Optional[bytes]:
        try:
            doc = self.images.find_one({"_id": ObjectId(image_id)})
        except Exception:
            return None
        return bytes(doc["data"]) if doc else None

    def save_generation(self, doc: Dict[str, Any]) -> str:
        doc = dict(doc)
        doc["created_at"] = now()
        doc.setdefault("review", None)
        if not self.settings.store_inputs:
            doc["input"] = {"redacted": True}
        return str(self.generations.insert_one(doc).inserted_id)

    def list_generations(self, mode: Optional[str] = None, limit: int = 50) -> List[dict]:
        q = {"mode": mode} if mode else {}
        cur = self.generations.find(q).sort("created_at", DESCENDING).limit(limit)
        return [self._public(d) for d in cur]

    def get_generation(self, gid: str) -> Optional[dict]:
        try:
            d = self.generations.find_one({"_id": ObjectId(gid)})
        except Exception:
            return None
        return self._public(d) if d else None

    def review_generation(self, gid: str, review: dict) -> bool:
        update: Dict[str, Any] = {"review": {**review, "reviewed_at": now()}}
        if review.get("edited"):
            update["final_output"] = review["edited"]
        try:
            res = self.generations.update_one({"_id": ObjectId(gid)}, {"$set": update})
        except Exception:
            return False
        return res.matched_count == 1

    def delete_generation(self, gid: str) -> bool:
        try:
            d = self.generations.find_one({"_id": ObjectId(gid)})
        except Exception:
            return False
        if not d:
            return False
        for iid in d.get("image_ids", []):
            try:
                self.images.delete_one({"_id": ObjectId(iid)})
            except Exception:
                pass
        self.generations.delete_one({"_id": d["_id"]})
        return True

    @staticmethod
    def _public(d: dict) -> dict:
        d = dict(d)
        d["id"] = str(d.pop("_id"))
        if isinstance(d.get("created_at"), dt.datetime):
            d["created_at"] = d["created_at"].isoformat()
        r = d.get("review")
        if r and isinstance(r.get("reviewed_at"), dt.datetime):
            r["reviewed_at"] = r["reviewed_at"].isoformat()
        return d

    def list_voices(self) -> List[dict]:
        out = []
        for v in self.voices.find().sort("name", 1):
            v["id"] = str(v.pop("_id"))
            v.pop("created_at", None)
            out.append(v)
        return out

    def get_voice(self, vid: Optional[str]) -> Optional[dict]:
        if not vid:
            return None
        try:
            return self.voices.find_one({"_id": ObjectId(vid)})
        except Exception:
            return None

    def save_voice(self, voice: dict) -> str:
        res = self.voices.update_one({"name": voice["name"]}, {"$set": {**voice, "created_at": now()}}, upsert=True)
        if res.upserted_id:
            return str(res.upserted_id)
        return str(self.voices.find_one({"name": voice["name"]})["_id"])

    def delete_voice(self, vid: str) -> bool:
        try:
            return self.voices.delete_one({"_id": ObjectId(vid)}).deleted_count == 1
        except Exception:
            return False

    def log_request(self, mode: str, provider: str, latency_ms: float, status: str, error_code: str = "") -> None:
        self.logs.insert_one({"mode": mode, "provider": provider, "latency_ms": round(latency_ms, 1),
                              "status": status, "error_code": error_code, "created_at": now()})

    def metrics(self) -> dict:
        """Evaluation metrics computed from stored logs and reviews."""
        logs = list(self.logs.find({}, {"_id": 0}))
        gens = list(self.generations.find({}, {"mode": 1, "review": 1, "output": 1}))

        def pct(vals: List[float], p: float) -> Optional[float]:
            if not vals:
                return None
            s = sorted(vals)
            k = max(0, min(len(s) - 1, int(round(p * (len(s) - 1)))))
            return round(s[k], 1)

        by_mode: Dict[str, dict] = {}
        for m in ["text-to-text", "text-to-image", "image-to-text", "image-to-image"]:
            ml = [l for l in logs if l["mode"] == m]
            ok = [l["latency_ms"] for l in ml if l["status"] == "ok"]
            by_mode[m] = {
                "requests": len(ml),
                "errors": sum(1 for l in ml if l["status"] != "ok"),
                "p50_ms": pct(ok, 0.5),
                "p95_ms": pct(ok, 0.95),
            }
        reviewed = [g["review"] for g in gens if g.get("review")]
        approved = [r for r in reviewed if r.get("approved")]
        ratings = [r["rating"] for r in reviewed if r.get("rating")]
        secs = [r["seconds_to_approve"] for r in approved if r.get("seconds_to_approve") is not None]
        unsupported = sum(r.get("unsupported_claims", 0) for r in reviewed)
        total_claims = sum(r.get("total_claims", 0) for r in reviewed)
        ok_all = [l["latency_ms"] for l in logs if l["status"] == "ok"]
        flags_auto = [len((g.get("output") or {}).get("review_flags", [])) for g in gens if g.get("output")]
        return {
            "total_requests": len(logs),
            "error_rate": round(sum(1 for l in logs if l["status"] != "ok") / len(logs), 3) if logs else None,
            "p50_ms": pct(ok_all, 0.5),
            "p95_ms": pct(ok_all, 0.95),
            "share_under_3s": round(sum(1 for v in ok_all if v < 3000) / len(ok_all), 3) if ok_all else None,
            "generations": len(gens),
            "reviewed": len(reviewed),
            "approved": len(approved),
            "approval_rate": round(len(approved) / len(reviewed), 3) if reviewed else None,
            "avg_rating": round(sum(ratings) / len(ratings), 2) if ratings else None,
            "avg_seconds_per_approved": round(sum(secs) / len(secs), 1) if secs else None,
            "unsupported_claim_rate": round(unsupported / total_claims, 3) if total_claims else None,
            "avg_auto_flags": round(sum(flags_auto) / len(flags_auto), 2) if flags_auto else None,
            "by_mode": by_mode,
            "db_backend": self.backend,
        }
