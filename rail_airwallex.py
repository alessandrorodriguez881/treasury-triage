"""Single wrapper for every Airwallex call: login, token refresh, retries and request_id.

Rules (from the builder guide):
- amounts are in major units (100 means 100 USD)
- every write carries a UUID request_id; a retry resends the SAME body, so Airwallex dedupes it
- writes without a request_id (sandbox simulations) are never retried: a blind retry could double them
Never logs keys or tokens.
"""
from __future__ import annotations

import time
import uuid
from pathlib import Path

import requests

SANDBOX_BASE = "https://api.sandbox.airwallex.com"
TOKEN_TTL_SECONDS = 25 * 60  # tokens last 30 min; refresh early
RETRY_STATUSES = {429, 500, 502, 503, 504}


class AirwallexError(Exception):
    def __init__(self, status: int, code: str | None, message: str | None, path: str):
        super().__init__(f"{path} -> HTTP {status} {code}: {message}")
        self.status, self.code, self.message, self.path = status, code, message, path


def read_env(path: Path) -> dict:
    values = {}
    if not path.exists():  # sin .env: modo demo con el simulador (modo.py)
        return values
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


class AirwallexRail:
    def __init__(self, client_id: str, api_key: str, base: str = SANDBOX_BASE,
                 session=None, max_retries: int = 3, sleep=time.sleep, clock=time.monotonic):
        self._client_id, self._api_key = client_id, api_key
        self.base, self.max_retries = base, max_retries
        self._http = session or requests.Session()
        self._sleep, self._clock = sleep, clock
        self._token, self._token_until = None, 0.0

    @classmethod
    def from_env(cls, path: Path, **kwargs) -> "AirwallexRail":
        env = read_env(path)
        if env.get("AIRWALLEX_ENVIRONMENT", "SANDBOX").upper() != "SANDBOX":
            raise RuntimeError("This project only runs against the Airwallex SANDBOX")
        return cls(env["AIRWALLEX_CLIENT_ID"], env["AIRWALLEX_API_KEY"], **kwargs)

    # ---------- plumbing ----------
    def _login(self) -> None:
        r = self._http.post(f"{self.base}/api/v1/authentication/login", timeout=30,
                            headers={"x-client-id": self._client_id, "x-api-key": self._api_key})
        if not r.ok:
            raise _error(r, "/api/v1/authentication/login")
        self._token, self._token_until = r.json()["token"], self._clock() + TOKEN_TTL_SECONDS

    def _auth(self) -> dict:
        if self._token is None or self._clock() >= self._token_until:
            self._login()
        return {"Authorization": f"Bearer {self._token}"}

    def call(self, method: str, path: str, body: dict | None = None, params: dict | None = None):
        idempotent = method == "GET" or bool(body and body.get("request_id"))
        relogged = False
        attempt = 0
        while True:
            try:
                r = self._http.request(method, f"{self.base}{path}", json=body, params=params,
                                       headers=self._auth(), timeout=30)
            except (requests.ConnectionError, requests.Timeout):
                if not idempotent or attempt >= self.max_retries:
                    raise
                attempt += 1
                self._sleep(0.5 * 2 ** attempt)
                continue
            if r.status_code == 401 and not relogged:
                self._token, relogged = None, True
                continue
            if r.status_code in RETRY_STATUSES and idempotent and attempt < self.max_retries:
                attempt += 1
                self._sleep(0.5 * 2 ** attempt)
                continue
            if not r.ok:
                raise _error(r, path)
            return r.json() if r.content else {}

    # ---------- accounts and balances ----------
    def global_accounts(self) -> list[dict]:
        return self.call("GET", "/api/v1/global_accounts").get("items", [])

    def balances(self) -> dict[str, dict]:
        return {b["currency"]: b for b in self.call("GET", "/api/v1/balances/current")}

    def create_global_account(self, currency: str, country_code: str, nick_name: str,
                              transfer_methods=("LOCAL",), request_id: str | None = None) -> dict:
        return self.call("POST", "/api/v1/global_accounts/create", {
            "request_id": request_id or str(uuid.uuid4()),
            "country_code": country_code,
            "nick_name": nick_name,
            "required_features": [{"currency": currency, "transfer_method": m} for m in transfer_methods],
        })

    # ---------- payouts ----------
    def beneficiaries(self) -> list[dict]:
        return self.call("GET", "/api/v1/beneficiaries", params={"page_size": 100}).get("items", [])

    def create_beneficiary(self, beneficiary: dict, nickname: str, transfer_methods=("LOCAL",)) -> dict:
        return self.call("POST", "/api/v1/beneficiaries/create", {
            "beneficiary": beneficiary, "nickname": nickname, "transfer_methods": list(transfer_methods)})

    def create_transfer(self, request_id: str, beneficiary_id: str, source_currency: str,
                        transfer_currency: str, transfer_amount: float, reference: str,
                        reason: str = "business_expenses", transfer_method: str = "LOCAL",
                        fee_paid_by: str = "PAYER") -> dict:
        """After an error we look the request_id up before giving up, so the ledger never marks as failed
        (and pays again) a transfer whose first try actually went through."""
        body = {
            "request_id": request_id,
            "beneficiary_id": beneficiary_id,
            "source_currency": source_currency,
            "transfer_currency": transfer_currency,
            "transfer_amount": f"{transfer_amount:.2f}",
            "transfer_method": transfer_method,
            "reason": reason,
            "reference": reference,
            "fee_paid_by": fee_paid_by,
        }
        try:
            return self.call("POST", "/api/v1/transfers/create", body)
        except (AirwallexError, requests.RequestException):
            found = self.find_transfer(request_id)
            if found:
                return found
            raise

    def find_transfer(self, request_id: str) -> dict | None:
        items = self.call("GET", "/api/v1/transfers", params={"request_id": request_id}).get("items", [])
        return items[0] if items else None

    def get_transfer(self, transfer_id: str) -> dict:
        return self.call("GET", f"/api/v1/transfers/{transfer_id}")

    # ---------- FX ----------
    def current_rate(self, buy_currency: str, sell_currency: str, buy_amount: float | None = None) -> dict:
        params = {"buy_currency": buy_currency, "sell_currency": sell_currency}
        if buy_amount is not None:
            params["buy_amount"] = f"{buy_amount:.2f}"
        return self.call("GET", "/api/v1/fx/rates/current", params=params)

    def create_quote(self, sell_currency: str, buy_currency: str, buy_amount: float,
                     validity: str = "HR_1") -> dict:
        """Locks a rate, so an approver sees the exact cost. No request_id: never retried (it moves no money)."""
        return self.call("POST", "/api/v1/fx/quotes/create", {
            "sell_currency": sell_currency, "buy_currency": buy_currency,
            "buy_amount": f"{buy_amount:.2f}", "validity": validity})

    def get_quote(self, quote_id: str) -> dict:
        return self.call("GET", f"/api/v1/fx/quotes/{quote_id}")

    def create_conversion(self, request_id: str, sell_currency: str, buy_currency: str,
                          buy_amount: float, quote_id: str | None = None) -> dict:
        """Airwallex rejects a reused request_id instead of returning the first conversion. So after an
        error we look the request_id up: if the first try went through, we return it (never convert twice).
        With a quote_id the conversion runs at the quote's rate (the one the approver saw)."""
        body = {"request_id": request_id, "sell_currency": sell_currency, "buy_currency": buy_currency,
                "buy_amount": f"{buy_amount:.2f}"}
        if quote_id:
            body["quote_id"] = quote_id
        try:
            return self.call("POST", "/api/v1/fx/conversions/create", body)
        except (AirwallexError, requests.RequestException):
            found = self.find_conversion(request_id)
            if found:
                return found
            raise

    def find_conversion(self, request_id: str) -> dict | None:
        items = self.call("GET", "/api/v1/fx/conversions", params={"request_id": request_id}).get("items", [])
        return items[0] if items else None

    def get_conversion(self, conversion_id: str) -> dict:
        return self.call("GET", f"/api/v1/fx/conversions/{conversion_id}")

    # ---------- incoming money ----------
    def deposits(self, from_created_at: str, page_size: int = 100) -> list[dict]:
        """Deposits into the global accounts since a UTC time (ISO 8601), page by page: payer, reference, amount."""
        out, page = [], 0
        while True:
            data = self.call("GET", "/api/v1/deposits", params={"page_num": page, "page_size": page_size,
                                                               "from_created_at": from_created_at})
            out += data.get("items", [])
            if not data.get("has_more"):
                return out
            page += 1

    # ---------- sandbox only ----------
    def simulate_transfer_status(self, transfer_id: str, next_status: str) -> dict:
        return self.call("POST", f"/api/v1/simulation/transfers/{transfer_id}/transition",
                         {"next_status": next_status})

    def simulate_deposit(self, global_account_id: str, amount: float, reference: str,
                         payer_name: str = "Demo customer", status: str = "SETTLED") -> dict:
        return self.call("POST", "/api/v1/simulation/deposit/create", {
            "global_account_id": global_account_id,
            "amount": amount,
            "payer_name": payer_name,
            "payer_bankname": "Sandbox Bank",
            "payer_country": "US",
            "reference": reference,
            "status": status,
        })


def _error(r, path: str) -> AirwallexError:
    try:
        data = r.json()
    except ValueError:
        data = {}
    message = data.get("message")
    errors = data.get("errors") or (data.get("details") or {}).get("errors")
    if errors:
        message = f"{message} {errors}"
    return AirwallexError(r.status_code, data.get("code"), message, path)
