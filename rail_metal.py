"""Single wrapper for the agent's payments on Metal testnet: x402 offers paid in *USDZ with the `metal-agent` CLI
(npm @metalntwx/metal-agent), plus the reads around them.

Rules (the same spirit as rail_airwallex.py):
- amounts are in *USDZ major units (0.18 means 18 cents); on chain *USDZ has 18 decimals
- before signing, the agent reads the seller's offer and pays only an `exact` *USDZ offer on testnet, to the
  supplier's address on file, for exactly the invoice amount; `--max` makes the CLI refuse anything above it too
- a refused payment moved nothing and may be tried again; a payment "in doubt" (signed, maybe settled) is never
  signed again: it is looked up at Metal's facilitator, whose settlement id ends in the payment's nonce
- `pay_once` ties a payment to the shared ledger (triage_core.ledger), so one invoice is paid once across runs
- the private key never passes through here: the CLI reads the key file, and this module only reads its
  `address`. The RPC URL is a credential (its id spends credit), so it goes in the child's environment, never
  in its arguments, and is hidden in every message.
- under a mandate (`mandate=` a credential file), the agent pays from its owner's Metal account within limits the
  chain enforces (`metal-agent pay --mandate`): no `--key` or `--max`, and the payer it looks payments up by is
  the owner's account, read from the credential (layer 1's `sub`)
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import requests

from rail_airwallex import read_env

CLI_VERSION = "0.3.3"
NETWORK = "testnet"
CHAIN = "eip155:63825"
FACILITATOR = "https://x402-testnet.metalntwx.com/x402/facilitator"
EXPLORER = "https://testnet.metalntwx.com"
USDZ = "0x000000000000000000000000000000000000B5d5"
USDZ_DECIMALS = 18
# Where `metal/preparar.py` installs the CLI at versions that work together (outside OneDrive, and not in AppData,
# which the Microsoft Store Python redirects)
RUNTIME = Path(os.environ.get("TT_METAL_RUNTIME") or Path.home() / ".treasury-triage-metal")

_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
_HASH = re.compile(r"^0x[0-9a-fA-F]{64}$")
_CMD_METACHARS = set('&|<>^%!"')
IN_DOUBT_MARKS = ("in doubt", "may have settled", "may have been sent", "can land until")


class MetalError(Exception):
    def __init__(self, message: str, reason: str | None = None, nonce: str | None = None,
                 transaction: str | None = None):
        super().__init__(message)
        self.reason, self.nonce, self.transaction = reason, nonce, transaction


class OfferMismatch(MetalError):
    """The seller asks for something other than the invoice: another payee, amount, token or network. Nothing
    was signed."""


class PaymentRefused(MetalError):
    """Refused before signing, at verification or at settlement for a reason that moves nothing: nothing was
    paid, so a new attempt is safe."""


class PaymentInDoubt(MetalError):
    """Signed, and it may have settled: never pay it again. Look it up by its nonce (`find_settlement`)."""


class NeedsRpc(MetalError):
    """Reading the chain (balance, receipts) needs an RPC endpoint: METAL_RPC_URL."""


@dataclass(frozen=True)
class Offer:
    pay_to: str
    amount: Decimal
    network: str
    asset: str
    scheme: str
    description: str = ""


@dataclass
class Payment:
    status: str                      # PAID, or NOT_ASKED when the URL asked for no payment (say, already paid)
    transaction: str | None
    amount: Decimal | None
    payee: str | None
    payer: str | None = None
    nonce: str | None = None
    found_by: str = "cli"            # cli: the CLI saw it settle; facilitator: looked up after a doubt
    body: object = None
    headers: dict = field(default_factory=dict)
    doubt: str | None = None         # why it had to be looked up, when it was

    @property
    def explorer(self) -> str | None:
        return f"{EXPLORER}/tx/{self.transaction}" if self.transaction else None


def usdz(units) -> Decimal:
    """Base units (18 decimals) to *USDZ."""
    return Decimal(int(units)) / (Decimal(10) ** USDZ_DECIMALS)


def _cents(amount) -> Decimal:
    try:
        value = Decimal(str(amount))
    except InvalidOperation:
        raise ValueError(f"not an amount: {amount!r}") from None
    if value <= 0 or value != value.quantize(Decimal("0.01")):
        raise ValueError(f"amounts are positive *USDZ to the cent, such as 0.18; got {amount!r}")
    return value


def default_cli() -> list[str]:
    """`node <metal-agent>/dist/index.js` from METAL_AGENT_JS or the local install; else `npx`.

    Direct `node` is preferred: on Windows `npx` is a .cmd, so cmd.exe would read characters such as & in a URL,
    and npx resolves peer versions afresh (on 30-oct it pulled @x402/fetch 2.29.0, which breaks `pay` 0.3.3)."""
    node = shutil.which("node") or "node"
    js = os.environ.get("METAL_AGENT_JS")
    local = RUNTIME / "node_modules" / "@metalntwx" / "metal-agent" / "dist" / "index.js"
    if js:
        return [node, js]
    if local.exists():
        return [node, str(local)]
    return [shutil.which("npx") or "npx", "-y", f"@metalntwx/metal-agent@{CLI_VERSION}"]


def mandate_principal(credential: Path) -> str:
    """The owner's Metal account a mandate credential pays from: layer 1's `sub` (it holds no secret)."""
    try:
        layer1 = json.loads(Path(credential).read_text(encoding="utf-8"))["layer1"]
        payload = layer1.split("~", 1)[0].split(".")[1]
        sub = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["sub"]
    except (OSError, ValueError, KeyError, IndexError, binascii.Error):
        raise MetalError(f"{credential} is not a mandate credential") from None
    if not _ADDRESS.match(sub or ""):
        raise MetalError(f"{credential} names no account")
    return sub


class MetalRail:
    def __init__(self, key_file: Path | None, cli: list[str] | None = None, rpc_url: str | None = None,
                 http=None, run=subprocess.run, timeout: float = 180, sleep=time.sleep, clock=time.time,
                 lookups: int = 5, mandate: Path | None = None, mandate_key_file: Path | None = None):
        if key_file is None and mandate is None:
            raise ValueError("the agent pays with its key file or under a mandate")
        self.key_file = Path(key_file) if key_file else None
        self.mandate = Path(mandate) if mandate else None
        self.mandate_key_file = Path(mandate_key_file) if mandate_key_file else None
        self.cli = list(cli) if cli else default_cli()
        self._rpc_url = rpc_url or None
        self._http = http or requests.Session()
        self._run, self.timeout, self._sleep, self._clock = run, timeout, sleep, clock
        self.lookups = lookups
        self._address: str | None = None

    @classmethod
    def from_env(cls, env_path: Path, key_file: Path, **kwargs) -> "MetalRail":
        env = read_env(env_path)
        if env.get("METAL_NETWORK", NETWORK) != NETWORK:
            raise RuntimeError("This project only runs against Metal TESTNET")
        if env.get("METAL_AGENT_JS") and "cli" not in kwargs:
            kwargs["cli"] = [shutil.which("node") or "node", env["METAL_AGENT_JS"]]
        return cls(key_file, rpc_url=env.get("METAL_RPC_URL") or os.environ.get("METAL_RPC_URL"), **kwargs)

    def __repr__(self) -> str:
        paga = f"mandate={str(self.mandate)!r}" if self.mandate else f"key_file={str(self.key_file)!r}"
        return f"MetalRail({paga}, rpc={'set' if self._rpc_url else 'none'})"

    # ---------- plumbing ----------
    def _hide(self, text: str) -> str:
        if self._rpc_url:
            text = text.replace(self._rpc_url, "[RPC URL hidden]")
            endpoint = self._rpc_url.rstrip("/").rsplit("/", 1)[-1]
            if len(endpoint) >= 8:
                text = text.replace(endpoint, "[hidden]")
        return text

    def _cli(self, *args: str, rpc: bool = False) -> tuple[int, list[str], str]:
        """Runs one CLI command. Returns (exit code, stdout lines, the `error:` text or stderr)."""
        command = self.cli + list(args)
        if self.cli[0].lower().endswith((".cmd", ".bat")) and any(set(a) & _CMD_METACHARS for a in args):
            raise MetalError("an argument holds a character cmd.exe would read; set METAL_AGENT_JS to run node directly")
        env = dict(os.environ)
        env.pop("METAL_RPC_URL", None)
        if rpc:
            if not self._rpc_url:
                raise NeedsRpc("reading Metal testnet needs an RPC endpoint of your own: set METAL_RPC_URL "
                               "(console-testnet.metalntwx.com, or bought by x402 at rpc-testnet.metalntwx.com/v1)")
            env["METAL_RPC_URL"] = self._rpc_url
        done = self._run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                         timeout=self.timeout, env=env)
        out = (done.stdout or "").splitlines()
        err_lines = (done.stderr or "").splitlines()
        error = next((l[len("error: "):] for l in err_lines if l.startswith("error: ")), "\n".join(err_lines).strip())
        return done.returncode, out, self._hide(error)

    def _json_rpc(self, method: str, params: list):
        if not self._rpc_url:
            raise NeedsRpc("reading Metal testnet needs an RPC endpoint of your own: set METAL_RPC_URL")
        try:
            r = self._http.post(self._rpc_url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                                timeout=30)
            data = r.json()
        except (requests.RequestException, ValueError) as e:
            raise MetalError(f"the RPC endpoint could not be reached ({type(e).__name__})") from None
        if "error" in data:
            raise MetalError(self._hide(f"RPC {method}: {data['error'].get('message', data['error'])}"))
        return data["result"]

    # ---------- the agent ----------
    def address(self) -> str:
        """The address that pays: under a mandate, the owner's account; else the agent's, read from its key file
        (the only field this module reads)."""
        if self._address is None and self.mandate:
            self._address = mandate_principal(self.mandate)
        if self._address is None:
            try:
                address = json.loads(self.key_file.read_text(encoding="utf-8")).get("address", "")
            except (OSError, ValueError):
                raise MetalError(f"cannot read the key file {self.key_file}") from None
            if not _ADDRESS.match(address or ""):
                raise MetalError(f"{self.key_file} names no address")
            self._address = address
        return self._address

    def balance(self) -> Decimal:
        """The agent's *USDZ, read from the chain (USDZ `balanceOf`). Needs METAL_RPC_URL."""
        data = "0x70a08231" + self.address()[2:].lower().rjust(64, "0")
        return usdz(int(self._json_rpc("eth_call", [{"to": USDZ, "data": data}, "latest"]), 16))

    def fund(self) -> dict:
        """Asks the public faucet for *USDZ (testnet; the faucet sets the amount, and limits requests)."""
        code, out, error = self._cli("fund", self.address(), "--network", NETWORK)
        fields = _fields(out)
        granted = re.search(r" with ([\d.]+) \*USDZ", next((l for l in out if l.startswith("funded ")), ""))
        already = any("already holds" in l for l in out)
        if not granted and not already:
            raise MetalError(f"faucet: {error or f'no grant (code {code})'}", reason=error)
        return {"funded": bool(granted), "amount": Decimal(granted.group(1)) if granted else Decimal(0),
                "transaction": fields.get("transaction"), "explorer": fields.get("explorer"),
                "already_funded": already}

    # ---------- paying ----------
    def offer(self, url: str) -> tuple[Offer | None, object]:
        """What the URL asks to be paid, without paying: (the exact *USDZ offer on testnet, the body).
        (None, body) when it asks for no payment, for example an invoice that is already paid."""
        try:
            r = self._http.get(url, timeout=30)
        except requests.RequestException as e:
            raise MetalError(f"{url} could not be reached ({type(e).__name__}); nothing was signed") from None
        body = _body(r.text)
        if r.status_code != 402:
            if r.status_code >= 400:
                raise MetalError(f"{url} answered {r.status_code}; nothing was signed")
            return None, body
        header = r.headers.get("payment-required")
        try:
            required = json.loads(base64.b64decode(header or ""))
        except (ValueError, binascii.Error):
            raise OfferMismatch(f"{url} answered 402 without a readable offer; nothing was signed") from None
        for a in required.get("accepts", []):
            if a.get("scheme") == "exact" and a.get("network") == CHAIN and str(a.get("asset", "")).lower() == USDZ.lower():
                return Offer(a.get("payTo", ""), usdz(a.get("amount", 0)), a["network"], a["asset"], a["scheme"],
                             (required.get("resource") or {}).get("description", "")), body
        kinds = ", ".join(f"{a.get('scheme')} on {a.get('network')}" for a in required.get("accepts", [])) or "none"
        raise OfferMismatch(f"{url} offers no exact *USDZ payment on Metal testnet (offers: {kinds}); nothing was signed")

    def pay(self, url: str, amount, pay_to: str, method: str = "GET", body: str | None = None) -> Payment:
        """Pays the invoice at `url`: exactly `amount` *USDZ to `pay_to` (the supplier's address on file).

        After a doubt it looks the payment up at the facilitator by its nonce and returns it if it settled;
        if it cannot find it, it raises PaymentInDoubt and the caller must not pay again."""
        amount = _cents(amount)
        if not _ADDRESS.match(pay_to or ""):
            raise ValueError(f"pay_to must be a 0x address, not {pay_to!r}")
        offer, seen = self.offer(url)
        if offer is None:
            tx = seen.get("transaction") if isinstance(seen, dict) else None
            return Payment("NOT_ASKED", tx, None, pay_to, body=seen, found_by="seller")
        if offer.pay_to.lower() != pay_to.lower():
            raise OfferMismatch(f"the offer pays {offer.pay_to}, not the supplier's address on file {pay_to}; "
                                "nothing was signed", reason="payee_mismatch")
        if offer.amount != amount:
            raise OfferMismatch(f"the offer asks {offer.amount} *USDZ, not the invoice's {amount}; nothing was signed",
                                reason="amount_mismatch")
        started = self._clock()
        if self.mandate:  # the mandate is the limit, and the chain enforces it; the mandate key beside it signs
            args = ["pay", url, "--network", NETWORK, "--mandate", str(self.mandate)]
            if self.mandate_key_file:
                args += ["--mandate-key-file", str(self.mandate_key_file)]
        else:
            args = ["pay", url, "--network", NETWORK, "--key", str(self.key_file), "--max", f"{amount}"]
        if method != "GET":
            args += ["--method", method]
        if body is not None:
            args += ["--body", body]
        try:
            code, out, error = self._cli(*args)
        except subprocess.TimeoutExpired:
            return self._after_doubt(pay_to, amount, None, started, "the CLI did not answer in time")
        # What the CLI printed decides, not its exit code: on Windows, Node can crash on its way out after a
        # payment that settled (libuv "UV_HANDLE_CLOSING" assertion, exit 127, seen on 30-oct)
        fields = _fields(out)
        nonce = fields.get("nonce")
        if not fields.get("transaction"):
            if any(l.startswith("payment none") for l in out):
                return Payment("NOT_ASKED", None, None, pay_to, body=_body(fields.get("body", "")), found_by="seller")
            if any(l.startswith("payment unconfirmed") for l in out):
                return self._after_doubt(pay_to, amount, nonce, started, "signed, but the seller returned no settlement")
            text = error or f"metal-agent pay ended with code {code} and no settlement"
            if any(mark in text for mark in IN_DOUBT_MARKS) or (nonce and "nothing was paid" not in text):
                return self._after_doubt(pay_to, amount, nonce, started, text)
            raise PaymentRefused(text, reason=_reason(text), nonce=nonce)
        paid = Payment("PAID", fields["transaction"], _amount(fields.get("amount")), fields.get("payee"),
                       fields.get("payer"), nonce, body=_body(fields.get("body", "")), headers=_headers(out))
        if (paid.payee or "").lower() != pay_to.lower() or paid.amount != amount:
            raise MetalError(f"PAID in {paid.transaction}, but to {paid.payee} for {paid.amount} *USDZ instead of "
                             f"{pay_to} for {amount}: a person must look at it", transaction=paid.transaction)
        return paid

    def _after_doubt(self, pay_to: str, amount: Decimal, nonce: str | None, started: float, why: str) -> Payment:
        for attempt in range(self.lookups):
            found = self.find_settlement(pay_to, nonce=nonce, amount=amount, since_ms=int(started * 1000) - 120_000)
            if found:
                return Payment("PAID", found["transaction"], usdz(found["amount"]), found["payTo"], found["payer"],
                               nonce or found["id"].rsplit(":", 1)[-1], found_by="facilitator", doubt=why)
            if attempt < self.lookups - 1:
                self._sleep(3)
        raise PaymentInDoubt(f"{why}; not found at the facilitator yet: do not pay again, look it up by its nonce",
                             reason="in_doubt", nonce=nonce)

    # ---------- looking payments up ----------
    def settlements(self, pay_to: str, since_ms: int = 0) -> list[dict]:
        """Payments Metal's facilitator settled to `pay_to` since `since_ms` (public, no RPC, all pages)."""
        out, since = [], max(0, int(since_ms))
        while True:
            try:
                r = self._http.get(f"{FACILITATOR}/settlements", params={"payTo": pay_to, "since": since}, timeout=30)
                rows = r.json().get("settlements", []) if r.ok else None
            except (requests.RequestException, ValueError):
                rows = None
            if rows is None:
                raise MetalError("the facilitator's settlements could not be read; retry shortly")
            new = [s for s in rows if s.get("id") not in {o.get("id") for o in out}]
            out += new
            if len(rows) < 500 or not new:
                return out
            since = rows[-1]["settledAt"]

    def find_settlement(self, pay_to: str, nonce: str | None = None, transaction: str | None = None,
                        amount=None, since_ms: int = 0) -> dict | None:
        """One settlement of ours to `pay_to`: by nonce (the tail of its id), or by transaction. Without either, the
        only one from this agent for `amount` since `since_ms`; two such make it ambiguous (None)."""
        me = self.address().lower()
        ours = [s for s in self.settlements(pay_to, since_ms) if str(s.get("payer", "")).lower() == me]
        if nonce:
            return next((s for s in ours if s.get("id", "").lower().endswith(nonce.lower())), None)
        if transaction:
            return next((s for s in ours if str(s.get("transaction", "")).lower() == transaction.lower()), None)
        if amount is not None:
            same = [s for s in ours if usdz(s.get("amount", 0)) == _cents(amount)]
            return same[0] if len(same) == 1 else None
        return None

    def payment_status(self, transaction: str, pay_to: str | None = None) -> dict:
        """A payment's state. With METAL_RPC_URL, from the chain (`metal-agent status`: success or reverted, block,
        transfers); without it, from the facilitator's record of settlements to `pay_to`."""
        if not _HASH.match(transaction or ""):
            raise ValueError("a transaction hash is 0x and 64 hex digits")
        if self._rpc_url:
            code, out, error = self._cli("status", transaction, "--network", NETWORK, rpc=True)
            fields = _fields(out)
            if not fields.get("status"):
                raise MetalError(f"status: {error or f'no receipt (code {code})'}")
            return {"source": "chain", "status": fields.get("status"), "block": fields.get("block"),
                    "transfers": [l[len("transfer "):] for l in out if l.startswith("transfer ")],
                    "explorer": f"{EXPLORER}/tx/{transaction}"}
        if not pay_to:
            raise NeedsRpc("without METAL_RPC_URL, name the payee (pay_to) to look it up at the facilitator")
        found = self.find_settlement(pay_to, transaction=transaction)
        return {"source": "facilitator", "status": "settled" if found else "not_found",
                "amount": usdz(found["amount"]) if found else None, "settled_at": found.get("settledAt") if found else None,
                "explorer": f"{EXPLORER}/tx/{transaction}"}


def pay_once(rail: MetalRail, ledger, op_key: str, url: str, amount, pay_to: str, counterparty: str) -> Payment:
    """One invoice, one payment, across runs, with the shared ledger (triage_core.ledger.Ledger).

    - already PAID: the ledger raises DuplicatePaymentError, as for Airwallex transfers
    - an attempt left in doubt (or cut short) is only looked up at the facilitator, never signed again
    - a refused attempt, or an offer that does not match, is FAILED: the next run is a new attempt
    - the supplier saying "already paid" counts only if the facilitator shows that payment came from this agent"""
    amount = _cents(amount)
    earlier = ledger.attempts(op_key)
    entry = ledger.begin(op_key, "x402_payment", float(amount), "*USDZ", counterparty)
    if earlier and earlier[-1] is entry:  # an earlier run began this attempt and never closed it
        nonce = (entry.provider_id or "").removeprefix("nonce:") or None
        since = _ms(entry.updated_at) - 300_000
        found = rail.find_settlement(pay_to, nonce=None if nonce == "unknown" else nonce, amount=amount, since_ms=since)
        if found:
            ledger.update(entry, "PAID", found["transaction"])
            return Payment("PAID", found["transaction"], usdz(found["amount"]), found["payTo"], found["payer"],
                           nonce, found_by="facilitator")
        raise PaymentInDoubt(f"{op_key}: an earlier attempt may have settled and is not at the facilitator yet; "
                             "not paid again", reason="in_doubt", nonce=nonce)
    try:
        paid = rail.pay(url, amount, pay_to)
    except (PaymentRefused, OfferMismatch):
        ledger.update(entry, "FAILED")
        raise
    except PaymentInDoubt as e:
        ledger.update(entry, "SENT", f"nonce:{e.nonce or 'unknown'}")
        raise
    if paid.status == "NOT_ASKED":
        found = rail.find_settlement(pay_to, transaction=paid.transaction) if paid.transaction else None
        if not found:
            ledger.update(entry, "FAILED")
            raise MetalError(f"{op_key}: the supplier asks for no payment but shows no settlement from this agent")
        paid = Payment("PAID", found["transaction"], usdz(found["amount"]), found["payTo"], found["payer"],
                       found_by="facilitator", body=paid.body)
    ledger.update(entry, "PAID", paid.transaction)
    return paid


def _fields(lines: list[str]) -> dict[str, str]:
    """`name value` lines from the CLI; `body` takes the rest of the output (it can hold newlines)."""
    out = {}
    for i, line in enumerate(lines):
        name, _, value = line.partition(" ")
        if name == "body":
            out["body"] = "\n".join([value] + lines[i + 1:])
            break
        if name != "header" and name not in out:
            out[name] = value.strip()
    return out


def _headers(lines: list[str]) -> dict[str, str]:
    pairs = [l[len("header "):].split(": ", 1) for l in lines if l.startswith("header ") and ": " in l]
    return {k: v for k, v in pairs}


def _body(text: str):
    try:
        return json.loads(text)
    except ValueError:
        return text


def _amount(text: str | None) -> Decimal | None:
    m = re.match(r"([\d.]+)", text or "")
    return Decimal(m.group(1)) if m else None


def _reason(text: str) -> str:
    m = re.search(r"refused(?: at settlement| before signing)?: ([A-Za-z_:]+)", text)
    return m.group(1) if m else "refused"


def _ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp() * 1000)
