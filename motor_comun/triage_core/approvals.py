"""Piece 4: approvals bound to exactly what the approver saw.

An approval covers one action with one amount, currency, counterparty and evidence.
If any of those change, the old approval no longer matches and a new one is needed.
With a path, the book is saved after every change, so an approval survives between runs.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


@dataclass(frozen=True)
class ApprovalRequest:
    action: str
    amount: float
    currency: str
    counterparty: str
    reason: str
    evidence: tuple[str, ...] = ()

    @property
    def fingerprint(self) -> str:
        payload = {"action": self.action, "amount": round(self.amount, 2), "currency": self.currency,
                   "counterparty": self.counterparty, "evidence": list(self.evidence)}
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


@dataclass
class ApprovalBook:
    granted: dict[str, dict] = field(default_factory=dict)
    pending: dict[str, ApprovalRequest] = field(default_factory=dict)
    rejected: dict[str, dict] = field(default_factory=dict)
    path: Path | None = None

    @classmethod
    def load(cls, path: Path) -> "ApprovalBook":
        book = cls(path=path)
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            book.pending = {fp: _request(r) for fp, r in data.get("pending", {}).items()}
            for name in ("granted", "rejected"):
                setattr(book, name, {fp: {**d, "request": _request(d["request"])}
                                     for fp, d in data.get(name, {}).items()})
        return book

    def _save(self) -> None:
        if self.path:
            data = {"pending": {fp: asdict(r) for fp, r in self.pending.items()}}
            for name in ("granted", "rejected"):
                data[name] = {fp: {**d, "request": asdict(d["request"])} for fp, d in getattr(self, name).items()}
            self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def request(self, req: ApprovalRequest) -> str:
        self.pending[req.fingerprint] = req
        self._save()
        return req.fingerprint

    def approve(self, fingerprint: str, approver: str) -> None:
        req = self.pending.pop(fingerprint)
        self.granted[fingerprint] = {"request": req, "approver": approver, "at": _now()}
        self._save()

    def reject(self, fingerprint: str, approver: str = "") -> None:
        req = self.pending.pop(fingerprint, None)
        if req:
            self.rejected[fingerprint] = {"request": req, "approver": approver, "at": _now()}
            self._save()

    def is_approved(self, req: ApprovalRequest) -> bool:
        """True only if this exact action (same amount, currency, counterparty, evidence) was approved."""
        return req.fingerprint in self.granted


def _request(d: dict) -> ApprovalRequest:
    return ApprovalRequest(**{**d, "evidence": tuple(d.get("evidence", ()))})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
