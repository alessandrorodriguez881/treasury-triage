"""Piece 6: what a customer's message means for when they will pay, checked in code before it counts.

The AI layer reads messy text and proposes a signal. Code accepts it only if:
- the message exists and comes from that customer's own email domain (no spoofing another customer)
- the signal is one of the allowed kinds, whose effect on the odds is fixed in policy (not by the model)
- the quoted evidence appears word for word in the message (no invented evidence)
- the message has not produced a signal already
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from .collections_model import CollectionsModel, CustomerProfile
from .policy import SIGNAL_LIKELIHOOD


class SignalRejected(ValueError):
    pass


@dataclass(frozen=True)
class Signal:
    message_id: str
    customer: str
    signal: str
    quote: str

    @property
    def evidence(self) -> str:
        return f"{self.customer} email {self.message_id}: '{self.quote}'"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def validate(sig: Signal, messages: dict[str, dict], customer_domains: dict[str, str],
             recorded: list[Signal]) -> None:
    """Raise SignalRejected with the reason if the signal cannot be trusted."""
    msg = messages.get(sig.message_id)
    if msg is None:
        raise SignalRejected(f"unknown message {sig.message_id}")
    if sig.customer not in customer_domains:
        raise SignalRejected(f"{sig.customer} is not a customer we expect money from")
    sender_domain = msg["from"].rsplit("@", 1)[-1].lower()
    if sender_domain != customer_domains[sig.customer]:
        raise SignalRejected(f"{sig.message_id} was sent from {sender_domain}, not from {sig.customer}")
    if sig.signal not in SIGNAL_LIKELIHOOD:
        raise SignalRejected(f"signal must be one of {sorted(SIGNAL_LIKELIHOOD)}")
    if len(_norm(sig.quote)) < 8 or _norm(sig.quote) not in _norm(msg["body"]):
        raise SignalRejected("the evidence quote must be copied word for word from the email")
    if any(r.message_id == sig.message_id for r in recorded):
        raise SignalRejected(f"{sig.message_id} already produced a signal")


def apply_signals(profile: CustomerProfile, signals: list[Signal]) -> CustomerProfile:
    for s in signals:
        if s.customer == profile.customer:
            profile = CollectionsModel.apply_signal(profile, SIGNAL_LIKELIHOOD[s.signal], s.evidence)
    return profile
