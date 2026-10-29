"""Piece 5: our own record of every money movement, and protection against paying twice.

- One logical operation (e.g. "pay obligation INV-7") gets one request_id; retries reuse it.
- A new attempt after a confirmed failure gets a new request_id (a replacement).
- An obligation that already has a successful payment cannot be paid again.
- Every change is also appended to an audit trail (`<ledger>.audit.jsonl`) where each event carries the hash of
  the one before. `verify()` recomputes the chain and checks the ledger against it: an edited event, a deleted one
  or a ledger entry changed by hand shows up.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import uuid

IN_FLIGHT = {"CREATED", "SUBMITTED", "SCHEDULED", "PROCESSING", "SENT"}
SUCCESS = {"PAID", "SETTLED", "SUCCEEDED", "COMPLETED"}
FAILED = {"CANCELLED", "FAILED", "REJECTED"}


class DuplicatePaymentError(RuntimeError):
    pass


@dataclass
class Entry:
    op_key: str
    attempt: int
    request_id: str
    kind: str
    amount: float
    currency: str
    counterparty: str
    status: str
    provider_id: str | None
    updated_at: str


@dataclass
class AuditCheck:
    ok: bool
    events: int          # events in the trail, all with a valid hash chain if ok
    entries_checked: int  # ledger entries that match their last event
    before_trail: int     # entries recorded before the trail existed (nothing to check them against)
    problem: str = ""


class Ledger:
    def __init__(self, path: Path | None = None):
        self.path = path
        self.entries: list[Entry] = []
        if path and path.exists():
            self.entries = [Entry(**e) for e in json.loads(path.read_text(encoding="utf-8"))]

    def _save(self) -> None:
        if self.path:
            self.path.write_text(json.dumps([asdict(e) for e in self.entries], indent=2), encoding="utf-8")

    @property
    def audit_path(self) -> Path | None:
        return self.path.with_name(self.path.stem + ".audit.jsonl") if self.path else None

    def _events(self) -> list[dict]:
        if not self.audit_path or not self.audit_path.exists():
            return []
        return [json.loads(line) for line in self.audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _audit(self, event: str, entry: Entry) -> None:
        """Append one event chained to the last one in the file (read from disk: other processes may write too)."""
        if not self.audit_path:
            return
        events = self._events()
        record = {"event": event, **asdict(entry), "prev": events[-1]["hash"] if events else ""}
        record["hash"] = _digest(record)
        with self.audit_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, sort_keys=True) + "\n")

    def verify(self) -> AuditCheck:
        events, prev, last = self._events(), "", {}
        for n, record in enumerate(events, 1):
            body = {k: v for k, v in record.items() if k != "hash"}
            if record.get("prev") != prev:
                return AuditCheck(False, n - 1, 0, 0, f"event {n} does not follow event {n - 1}: one was removed or reordered")
            if _digest(body) != record.get("hash"):
                return AuditCheck(False, n - 1, 0, 0, f"event {n} was changed after it was written")
            prev = record["hash"]
            last[(record["op_key"], record["attempt"])] = record
        checked = before = 0
        for e in self.entries:
            record = last.get((e.op_key, e.attempt))
            if record is None:
                before += 1
                continue
            if any(record[k] != v for k, v in asdict(e).items()):
                return AuditCheck(False, len(events), checked, before,
                                  f"ledger entry {e.op_key} (attempt {e.attempt}) differs from its audit trail")
            checked += 1
        return AuditCheck(True, len(events), checked, before)

    def attempts(self, op_key: str) -> list[Entry]:
        return [e for e in self.entries if e.op_key == op_key]

    def begin(self, op_key: str, kind: str, amount: float, currency: str, counterparty: str) -> Entry:
        """Return the entry to send. Reuses the request_id of an unfinished attempt (a retry)."""
        tries = self.attempts(op_key)
        if any(e.status in SUCCESS for e in tries):
            raise DuplicatePaymentError(f"{op_key} is already paid")
        last = tries[-1] if tries else None
        if last and last.status not in FAILED:
            return last  # retry of the same operation: same request_id
        entry = Entry(op_key, len(tries) + 1, str(uuid.uuid4()), kind, round(amount, 2), currency,
                      counterparty, "CREATED", None, _now())
        self.entries.append(entry)
        self._save()
        self._audit("begin", entry)
        return entry

    def update(self, entry: Entry, status: str, provider_id: str | None = None) -> None:
        entry.status = status.upper()
        entry.provider_id = provider_id or entry.provider_id
        entry.updated_at = _now()
        self._save()
        self._audit("update", entry)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(record: dict) -> str:
    return hashlib.sha256(json.dumps({k: v for k, v in record.items() if k != "hash"}, sort_keys=True)
                          .encode()).hexdigest()
