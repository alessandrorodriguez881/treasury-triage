"""Offline tests for the Airwallex wrapper: a fake HTTP session, no network, no keys."""
import json
import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rail_airwallex import AirwallexError, AirwallexRail


class FakeResponse:
    def __init__(self, status, data=None):
        self.status_code, self._data = status, data if data is not None else {}
        self.ok = 200 <= status < 300
        self.content = json.dumps(self._data).encode()

    def json(self):
        return self._data


class FakeSession:
    """Replays scripted responses for non-login calls and records every request."""

    def __init__(self, script):
        self.script, self.calls, self.logins = list(script), [], 0

    def post(self, url, headers=None, timeout=None):
        assert url.endswith("/authentication/login")
        self.logins += 1
        return FakeResponse(201, {"token": f"t{self.logins}"})

    def request(self, method, url, json=None, params=None, headers=None, timeout=None):
        self.calls.append({"method": method, "url": url, "body": json, "params": params,
                           "auth": headers["Authorization"]})
        nxt = self.script.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


def rail(script, now=None):
    clock = now or (lambda: 0.0)
    return AirwallexRail("id", "key", session=FakeSession(script), sleep=lambda s: None, clock=clock)


def test_retry_resends_the_same_request_id():
    r = rail([FakeResponse(503), FakeResponse(201, {"id": "ga_1", "status": "PROCESSING"})])
    out = r.create_global_account("USD", "US", "test")
    calls = r._http.calls
    assert out["id"] == "ga_1" and len(calls) == 2
    assert calls[0]["body"]["request_id"] == calls[1]["body"]["request_id"]


def test_simulation_without_request_id_is_never_retried():
    r = rail([FakeResponse(500), FakeResponse(201, {"status": "PENDING"})])
    with pytest.raises(AirwallexError) as e:
        r.simulate_deposit("ga_1", 100, "ref")
    assert e.value.status == 500 and len(r._http.calls) == 1  # a blind retry could deposit twice


def test_timeout_on_write_without_request_id_is_not_retried():
    r = rail([requests.Timeout(), FakeResponse(201, {})])
    with pytest.raises(requests.Timeout):
        r.simulate_deposit("ga_1", 100, "ref")
    assert len(r._http.calls) == 1


def test_expired_token_triggers_one_relogin():
    r = rail([FakeResponse(401), FakeResponse(200, {"items": []})])
    assert r.global_accounts() == []
    assert r._http.logins == 2
    assert [c["auth"] for c in r._http.calls] == ["Bearer t1", "Bearer t2"]


def test_client_errors_surface_the_airwallex_code_without_retry():
    r = rail([FakeResponse(400, {"code": "validation_failed", "message": "amount must be positive"})])
    with pytest.raises(AirwallexError) as e:
        r.create_global_account("USD", "US", "test")
    assert e.value.code == "validation_failed" and len(r._http.calls) == 1


def test_token_is_reused_until_close_to_expiry():
    t = [0.0]
    r = rail([FakeResponse(200, {"items": []})] * 3, now=lambda: t[0])
    r.global_accounts()
    r.global_accounts()
    assert r._http.logins == 1
    t[0] = 26 * 60  # past the early-refresh point (25 min)
    r.global_accounts()
    assert r._http.logins == 2


def test_gives_up_after_max_retries():
    r = rail([FakeResponse(503)] * 4)
    with pytest.raises(AirwallexError):
        r.global_accounts()
    assert len(r._http.calls) == 4  # 1 try + 3 retries


def test_conversion_lost_response_returns_the_original_instead_of_converting_again():
    # 1st try converts but the answer is lost (503); Airwallex rejects the retry's reused request_id
    r = rail([FakeResponse(503), FakeResponse(400, {"code": "invalid_argument", "message": "duplicate request_id"}),
              FakeResponse(200, {"items": [{"id": "conv_1", "request_id": "fx-1", "status": "SETTLED"}]})])
    out = r.create_conversion("fx-1", "USD", "EUR", 7812.85)
    assert out["id"] == "conv_1"
    creates = [c for c in r._http.calls if c["method"] == "POST"]
    assert [c["body"]["request_id"] for c in creates] == ["fx-1", "fx-1"]
    assert creates[0]["body"]["buy_amount"] == "7812.85" and "sell_amount" not in creates[0]["body"]
    assert r._http.calls[-1]["params"] == {"request_id": "fx-1"}


def test_quote_then_conversion_at_that_quote():
    r = rail([FakeResponse(201, {"quote_id": "q1", "sell_amount": 8797.68}),
              FakeResponse(201, {"id": "conv_2", "status": "SETTLED"})])
    q = r.create_quote("USD", "EUR", 7812.85)
    r.create_conversion("fx-3", "USD", "EUR", 7812.85, quote_id=q["quote_id"])
    quote_body, conv_body = r._http.calls[0]["body"], r._http.calls[1]["body"]
    assert quote_body == {"sell_currency": "USD", "buy_currency": "EUR", "buy_amount": "7812.85", "validity": "HR_1"}
    assert conv_body["quote_id"] == "q1"


def test_conversion_real_error_is_not_hidden_by_the_lookup():
    r = rail([FakeResponse(400, {"code": "amount_below_limit", "message": "min USD 1"}),
              FakeResponse(200, {"items": []})])
    with pytest.raises(AirwallexError) as e:
        r.create_conversion("fx-2", "USD", "EUR", 0.10)
    assert e.value.code == "amount_below_limit"


def test_transfer_that_went_through_is_found_not_paid_again():
    r = rail([FakeResponse(503)] * 4 + [FakeResponse(200, {"items": [{"id": "tr_9", "status": "SCHEDULED"}]})])
    out = r.create_transfer("pay-1", "ben_1", "USD", "USD", 12_000, "ref")
    assert out["id"] == "tr_9"  # the ledger records it as sent, so nobody creates a second one


def test_transfer_retry_keeps_the_ledger_request_id():
    r = rail([FakeResponse(502), FakeResponse(201, {"id": "tr_1", "status": "SCHEDULED"})])
    r.create_transfer("req-from-ledger", "ben_1", "USD", "USD", 100, "ref")
    assert [c["body"]["request_id"] for c in r._http.calls] == ["req-from-ledger"] * 2
    assert r._http.calls[0]["body"]["transfer_amount"] == "100.00"


def test_status_simulation_is_never_retried():
    r = rail([FakeResponse(503), FakeResponse(200, {"status": "PAID"})])
    with pytest.raises(AirwallexError):
        r.simulate_transfer_status("tr_1", "PAID")
    assert len(r._http.calls) == 1


def test_validation_errors_name_the_bad_field():
    body = {"code": "validation_failed", "message": "schema",
            "details": {"errors": [{"code": "010", "source": "beneficiary.bank_details.account_routing_value1"}]}}
    r = rail([FakeResponse(400, body)])
    with pytest.raises(AirwallexError) as e:
        r.create_beneficiary({}, "x")
    assert "account_routing_value1" in e.value.message
