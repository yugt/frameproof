"""MongoDB Atlas Vector Search memory for the self-improvement loop.

The collection stores ordinary metadata and English retrieval text in `content`.
The Atlas `frameproof_memory` index auto-embeds that field, so callers submit
natural-language queries without generating vectors in this process.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Iterable

from .brief import Brief
from .config import SETTINGS
from .verify import Verdict


@dataclass
class MemoryHit:
    type: str
    content: str
    score: float
    brief_id: str = ""
    metadata: dict | None = None

    def guidance(self) -> str:
        return f"[{self.type}; similarity={self.score:.3f}] {self.content}"


class MemoryStore:
    def __init__(self, uri: str | None = None, database: str | None = None,
                 collection: str | None = None, index: str | None = None):
        uri = uri or SETTINGS.mongodb_uri
        if not uri:
            raise RuntimeError("Set FP_MONGODB_URI in secrets.env to use MongoDB memory")
        import certifi
        from pymongo import MongoClient

        self.client = MongoClient(uri, tlsCAFile=certifi.where(), serverSelectionTimeoutMS=10000)
        self.collection = self.client[database or SETTINGS.mongodb_db][
            collection or SETTINGS.mongodb_collection
        ]
        self.index = index or SETTINGS.mongodb_vector_index

    def ping(self) -> None:
        self.client.admin.command("ping")

    def search(self, query: str, *, types: Iterable[str] = (), limit: int = 5,
               exclude_brief_id: str | None = None,
               min_score: float = 0.70) -> list[MemoryHit]:
        """Semantic search using Atlas Automated Embedding.

        Fetch extra candidates and filter client-side. This keeps the query
        compatible with existing indexes while still supporting memory types.
        """
        wanted = set(types)
        candidate_limit = max(20, limit * 4)
        pipeline = [
            {"$vectorSearch": {
                "index": self.index,
                "path": "content",
                "query": {"text": query},
                "numCandidates": max(50, candidate_limit * 5),
                "limit": candidate_limit,
            }},
            {"$set": {"_memory_score": {"$meta": "vectorSearchScore"}}},
            {"$project": {"embedding": 0}},
        ]
        out = []
        for doc in self.collection.aggregate(pipeline):
            kind = str(doc.get("type", ""))
            if wanted and kind not in wanted:
                continue
            if exclude_brief_id and str(doc.get("brief_id", "")) == exclude_brief_id:
                continue
            score = float(doc.get("_memory_score", 0.0))
            if score < min_score:
                continue
            content = str(doc.get("content", "")).strip()
            if not content:
                continue
            meta = {k: v for k, v in doc.items() if k not in {"_id", "content", "_memory_score"}}
            out.append(MemoryHit(kind, content, score,
                                 str(doc.get("brief_id", "")), meta))
            if len(out) >= limit:
                break
        return out

    def remember_requirement(self, brief: Brief) -> None:
        self._upsert(
            f"brief_requirement:{brief.id}",
            {"type": "brief_requirement", "brief_id": brief.id, "content": brief.text,
             "category": brief.category, "tags": ["brief", "requirement"]},
        )

    def remember_user_feedback(self, brief_id: str, requirement: str,
                               failure: str, fix: str) -> str:
        """Store a user's requirement -> observed failure -> successful fix lesson.

        The three documents use the same vocabulary as verifier-created memory, so
        the normal improve retrieval path can use them without a special case.
        """
        values = [brief_id.strip(), requirement.strip(), failure.strip(), fix.strip()]
        if not all(values):
            raise ValueError("brief_id, requirement, failure, and fix must all be non-empty")
        brief_id, requirement, failure, fix = values
        signature = hashlib.sha256("\n".join(values).encode()).hexdigest()[:20]
        common = {"brief_id": brief_id, "origin": "user_feedback", "feedback_id": signature}
        self._upsert(
            f"brief_requirement:user:{signature}",
            {**common, "type": "brief_requirement", "content": requirement,
             "tags": ["brief", "requirement", "user-feedback"]},
        )
        self._upsert(
            f"verification_failure:user:{signature}",
            {**common, "type": "verification_failure",
             "content": f"For the requirement '{requirement}', the observed failure was: {failure}",
             "reasons": [failure], "tags": ["failure", "user-feedback"]},
        )
        self._upsert(
            f"successful_fix:user:{signature}",
            {**common, "type": "successful_fix",
             "content": (f"For the requirement '{requirement}', avoid this failure: {failure}. "
                         f"Apply this proven fix: {fix}"),
             "guidance": [fix], "tags": ["fix", "user-feedback"]},
        )
        return signature

    def remember_failure(self, brief: Brief, verdict: Verdict, attempt: int) -> None:
        public = [r for r in verdict.results if not r.passed and not r.hidden]
        details = "; ".join(f"{r.id} ({r.kind}): {r.detail}" for r in public)
        public_reasons = verdict.public_reasons()
        reasons = "; ".join(public_reasons)
        content = (
            f"Brief {brief.id} was blocked on attempt {attempt}. "
            f"Reasons: {reasons or 'verification failed'}. "
            f"Public evidence: {details or 'no public check details available'}."
        )
        doc = {
            "type": "verification_failure", "brief_id": brief.id, "content": content,
            "attempt": attempt, "decision": verdict.decision, "score": verdict.score,
            "reasons": public_reasons,
            "evidence": [asdict(r) for r in public],
        }
        signature = self._failure_signature(brief.id, public)
        self._upsert_occurrence(f"verification_failure:{signature}", doc)

    def remember_fix(self, brief: Brief, before: Verdict, after: Verdict,
                     repair_guidance: list[str], attempt: int) -> None:
        content = (
            f"Brief {brief.id} was repaired after a blocked attempt and accepted on attempt {attempt}. "
            f"The repair guidance was: {'; '.join(repair_guidance)}. "
            f"The final deterministic verification score was {after.score:.3f}."
        )
        public_before = [r for r in before.results if not r.passed and not r.hidden]
        doc = {
            "type": "successful_fix", "brief_id": brief.id, "content": content,
            "attempt": attempt, "previous_decision": before.decision,
            "final_decision": after.decision, "previous_score": before.score,
            "final_score": after.score, "guidance": repair_guidance,
        }
        signature = self._failure_signature(brief.id, public_before)
        self._upsert_occurrence(f"successful_fix:{signature}", doc)

    def _upsert(self, key: str, doc: dict) -> None:
        doc = dict(doc)
        doc.update({"memory_key": key, "updated_at": datetime.now(timezone.utc)})
        self.collection.update_one({"memory_key": key}, {"$set": doc}, upsert=True)

    @staticmethod
    def _failure_signature(brief_id: str, results: list) -> str:
        evidence = sorted(
            ({"kind": r.kind, "detail": r.detail, "severity": r.severity} for r in results),
            key=lambda item: (item["kind"], item["severity"], item["detail"]),
        )
        raw = json.dumps({"brief_id": brief_id, "evidence": evidence}, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()[:20]

    def _upsert_occurrence(self, key: str, doc: dict) -> None:
        now = datetime.now(timezone.utc)
        current = dict(doc)
        current.update({"memory_key": key, "updated_at": now})
        self.collection.update_one(
            {"memory_key": key},
            {
                "$set": current,
                "$setOnInsert": {"created_at": now},
                "$inc": {"occurrences": 1},
            },
            upsert=True,
        )
