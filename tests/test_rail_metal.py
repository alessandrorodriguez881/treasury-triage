"""Offline tests for the Metal wrapper: a fake metal-agent CLI and a fake HTTP session, no network, no real keys.

The CLI outputs are the ones seen on Metal testnet on 30-oct (shortened)."""
import base64
import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
# the shared engine: next to the app (published repo) or two folders up (working folder)
sys.path.insert(0, str(next(p / "motor_comun" for p in (APP, *APP.parents) if (p / "motor_comun" / "triage_core").exists())))

from rail_metal import (CHAIN, USDZ, MetalError, MetalRail, NeedsRpc, OfferMismatch, PaymentInDoubt,
                        PaymentRefused, mandate_principal, pay_once)
from triage_core.ledger import DuplicatePaymentError, Ledger

AGENT = "0x7393A826Fac730EaBA3cc58a8407236D9De4580f"
SUPPLIER = "0xD3d4DF538fCfaE7fc6E055b72Dc55f1ac421247E"
OTHER = "0x1111111111111111111111111111111111111111"
PRIVATE = "0x" + "ab" * 32  # a fake key: it must never show up anywhere
URL = "http://127.0.0.1:4021/invoices/TS-10443/pay"
NONCE = "0x" + "7d" * 32
TX = "0x" + "8e" * 32
RPC = "https://rpc-testnet.metalntwx.com/v1/endpoint-id-that-is-a-credential"


class FakeResponse:
    def __init__(self, status, data=None, headers=None):
        self.status_code, self.headers = status, headers or {}
        self.text = json.dumps(data) if not isinstance(data, str) else data
        self.ok = 200 <= status < 300

    def json(self):
        return json.loads(self.text)


class FakeHttp:
    """GET: the seller's URL answers from `seller`, the facilitator from `settlements` (one list per lookup)."""

    def __init__(self, seller=None, settlements=(), rpc=None):
        self.seller = list(seller or [offer()])
        self.settlements = list(settlements)
        self.rpc, self.gets, self.posts = rpc, [], []

    def get(self, url, params=None, timeout=None):
        self.gets.append((url, params))
        if url.endswith("/settlements"):
            rows = self.settlements.pop(0) if len(self.settlements) > 1 else (self.settlements[0] if self.settlements else [])
            return FakeResponse(200, {"settlements": rows})
        return self.seller.pop(0) if len(self.seller) > 1 else self.seller[0]

    def post(self, url, json=None, timeout=None):
        self.posts.append((url, json))
        return FakeResponse(200, self.rpc)


class FakeCli:
    """Replays scripted CLI runs: (exit code, stdout, stderr), or an exception to raise."""

    def __init__(self, *script):
        self.script, self.calls = list(script), []

    def __call__(self, command, **kwargs):
        self.calls.append({"argv": command, "env": kwargs.get("env", {})})
        nxt = self.script.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        code, out, err = nxt
        return subprocess.CompletedProcess(command, code, out, err)


def offer(pay_to=SUPPLIER, units="180000000000000000", network=CHAIN, asset=USDZ, scheme="exact"):
    required = {"x402Version": 2, "resource": {"url": URL, "description": "Invoice TS-10443"},
                "accepts": [{"scheme": scheme, "network": network, "amount": units, "asset": asset, "payTo": pay_to,
                             "maxTimeoutSeconds": 60}]}
    return FakeResponse(402, {}, {"payment-required": base64.b64encode(json.dumps(required).encode()).decode()})


PAID_OUT = (f"nonce {NONCE}\nstatus 200\npayer {AGENT}\npayee {SUPPLIER}\namount 0.18 *USDZ\nnetwork {CHAIN}\n"
            f"transaction {TX}\nexplorer https://testnet.metalntwx.com/tx/{TX}\nheader x-invoice: TS-10443\n"
            'body {"invoice":"TS-10443","status":"paid"}\n')
IN_DOUBT_ERR = (f"error: {URL} answered 502: {{\"error\":\"upstream timeout\"}}; a payment was signed and may have "
                "settled; look it up by its nonce before paying again\n")


def settlement(payer=AGENT, nonce=NONCE, units="180000000000000000", tx=TX):
    return {"id": f"{CHAIN}:eip3009:{USDZ.lower()}:{payer.lower()}:{nonce}", "network": CHAIN, "scheme": "exact",
            "payer": payer, "payTo": SUPPLIER, "amount": units, "transaction": tx, "settledAt": 1793371353023}


@pytest.fixture
def key_file(tmp_path):
    path = tmp_path / "agente.key.json"
    path.write_text(json.dumps({"address": AGENT, "privateKey": PRIVATE}), encoding="utf-8")
    return path


def rail(key_file, cli, http=None, rpc_url=None, lookups=3):
    return MetalRail(key_file, cli=["node", "metal-agent.js"], run=cli, http=http or FakeHttp(), rpc_url=rpc_url,
                     sleep=lambda s: None, clock=lambda: 1793371350.0, lookups=lookups)


def test_the_private_key_is_never_passed_printed_or_returned(key_file, capsys):
    cli = FakeCli((0, PAID_OUT, ""))
    r = rail(key_file, cli)
    paid = r.pay(URL, "0.18", SUPPLIER)
    call = cli.calls[0]
    assert r.address() == AGENT
    assert call["argv"][call["argv"].index("--key") + 1] == str(key_file)  # the CLI reads the file itself
    seen = " ".join(call["argv"]) + json.dumps(call["env"]) + repr(r) + repr(paid) + "".join(capsys.readouterr())
    assert PRIVATE not in seen and PRIVATE[2:] not in seen


def test_pays_exactly_the_invoice_to_the_supplier_on_file(key_file):
    cli = FakeCli((0, PAID_OUT, ""))
    paid = rail(key_file, cli).pay(URL, Decimal("0.18"), SUPPLIER)
    argv = cli.calls[0]["argv"]
    assert argv[argv.index("--max") + 1] == "0.18" and argv[argv.index("--network") + 1] == "testnet"
    assert (paid.status, paid.transaction, paid.amount, paid.found_by) == ("PAID", TX, Decimal("0.18"), "cli")
    assert paid.explorer == f"https://testnet.metalntwx.com/tx/{TX}" and paid.body["status"] == "paid"


@pytest.mark.parametrize("seller, why", [
    (offer(pay_to=OTHER), "payee_mismatch"),             # invoice fraud changes the account: here, the address
    (offer(units="1800000000000000000"), "amount_mismatch"),  # 1.80 for an invoice of 0.18
])
def test_an_offer_that_differs_from_the_invoice_is_never_signed(key_file, seller, why):
    cli = FakeCli()
    with pytest.raises(OfferMismatch) as e:
        rail(key_file, cli, FakeHttp([seller])).pay(URL, "0.18", SUPPLIER)
    assert e.value.reason == why and cli.calls == []


@pytest.mark.parametrize("seller", [offer(asset="0x" + "22" * 20), offer(network="eip155:1"), offer(scheme="upto")])
def test_an_offer_in_another_token_network_or_scheme_is_never_signed(key_file, seller):
    cli = FakeCli()
    with pytest.raises(OfferMismatch):
        rail(key_file, cli, FakeHttp([seller])).pay(URL, "0.18", SUPPLIER)
    assert cli.calls == []


def test_a_refused_payment_moved_nothing_and_says_why(key_file):
    err = ("error: payment refused: transfer_would_fail (offer: 0.18 *USDZ on eip155:63825); nothing was paid\n")
    with pytest.raises(PaymentRefused) as e:
        rail(key_file, FakeCli((1, f"nonce {NONCE}\n", err))).pay(URL, "0.18", SUPPLIER)
    assert e.value.reason == "transfer_would_fail"


def test_the_cli_limit_refuses_before_signing(key_file):
    err = ("error: payment refused before signing: offer of 0.18 *USDZ is above the 0.1 *USDZ limit per payment; "
           "pass --max to allow it\n")
    with pytest.raises(PaymentRefused) as e:
        rail(key_file, FakeCli((1, "", err))).pay(URL, "0.18", SUPPLIER)
    assert e.value.nonce is None


def test_a_payment_in_doubt_is_found_at_the_facilitator_not_paid_again(key_file):
    # the supplier was paid but its answer was lost (502): the facilitator's record has it, by the nonce
    cli = FakeCli((1, f"nonce {NONCE}\n", IN_DOUBT_ERR))
    http = FakeHttp(settlements=[[settlement(nonce="0x" + "99" * 32, tx="0x" + "99" * 32), settlement()]])
    paid = rail(key_file, cli, http).pay(URL, "0.18", SUPPLIER)
    assert (paid.status, paid.transaction, paid.found_by) == ("PAID", TX, "facilitator")
    assert "may have settled" in paid.doubt and len(cli.calls) == 1
    assert http.gets[-1][1]["payTo"] == SUPPLIER


def test_a_payment_in_doubt_that_is_not_found_is_never_paid_again(key_file):
    cli = FakeCli((1, f"nonce {NONCE}\n", IN_DOUBT_ERR))
    http = FakeHttp(settlements=[[]])
    with pytest.raises(PaymentInDoubt) as e:
        rail(key_file, cli, http, lookups=3).pay(URL, "0.18", SUPPLIER)
    assert e.value.nonce == NONCE and len(cli.calls) == 1
    assert sum(1 for url, _ in http.gets if url.endswith("/settlements")) == 3


def test_a_settled_payment_counts_even_if_node_crashes_on_its_way_out(key_file):
    # seen on Windows: the payment settled, then libuv's assertion made Node exit with 127
    crash = "Assertion failed: !(handle->flags & UV_HANDLE_CLOSING), file src\\win\\async.c, line 76\n"
    paid = rail(key_file, FakeCli((127, PAID_OUT, crash))).pay(URL, "0.18", SUPPLIER)
    assert (paid.status, paid.transaction) == ("PAID", TX)


def test_a_cli_that_never_answers_is_in_doubt_and_looked_up(key_file):
    cli = FakeCli(subprocess.TimeoutExpired(["node"], 180))
    paid = rail(key_file, cli, FakeHttp(settlements=[[settlement()]])).pay(URL, "0.18", SUPPLIER)
    assert (paid.status, paid.found_by) == ("PAID", "facilitator")


def test_an_invoice_already_paid_asks_for_nothing_and_nothing_is_signed(key_file):
    cli = FakeCli()
    seller = FakeResponse(200, {"invoice": "TS-10443", "status": "paid", "transaction": TX},
                          {"x-invoice-status": "already_paid"})
    paid = rail(key_file, cli, FakeHttp([seller])).pay(URL, "0.18", SUPPLIER)
    assert (paid.status, paid.transaction) == ("NOT_ASKED", TX) and cli.calls == []


def test_pay_once_never_signs_twice_across_runs(key_file, tmp_path):
    libro = Ledger(tmp_path / "libro.json")
    first = FakeCli((1, f"nonce {NONCE}\n", IN_DOUBT_ERR))
    with pytest.raises(PaymentInDoubt):  # run 1: the answer is lost and the facilitator has nothing yet
        pay_once(rail(key_file, first, FakeHttp(settlements=[[]])), libro, "metal:r1:SAAS-3", URL, "0.18",
                 SUPPLIER, "ToolStack")
    assert libro.attempts("metal:r1:SAAS-3")[-1].status == "SENT"
    second = FakeCli()  # run 2: only a lookup, by the nonce kept in the ledger
    paid = pay_once(rail(key_file, second, FakeHttp(settlements=[[settlement()]])), Ledger(tmp_path / "libro.json"),
                    "metal:r1:SAAS-3", URL, "0.18", SUPPLIER, "ToolStack")
    assert (paid.status, paid.transaction, second.calls) == ("PAID", TX, [])
    with pytest.raises(DuplicatePaymentError):  # run 3: the ledger refuses a second payment
        pay_once(rail(key_file, FakeCli()), Ledger(tmp_path / "libro.json"), "metal:r1:SAAS-3", URL, "0.18",
                 SUPPLIER, "ToolStack")
    assert Ledger(tmp_path / "libro.json").verify().ok


def test_pay_once_after_a_refusal_makes_a_new_attempt(key_file, tmp_path):
    libro = Ledger(tmp_path / "libro.json")
    err = "error: payment refused: transfer_would_fail (offer: 0.18 *USDZ on eip155:63825); nothing was paid\n"
    with pytest.raises(PaymentRefused):
        pay_once(rail(key_file, FakeCli((1, "", err))), libro, "metal:r1:SAAS-3", URL, "0.18", SUPPLIER, "ToolStack")
    paid = pay_once(rail(key_file, FakeCli((0, PAID_OUT, ""))), libro, "metal:r1:SAAS-3", URL, "0.18", SUPPLIER,
                    "ToolStack")
    assert paid.status == "PAID"
    assert [e.status for e in libro.attempts("metal:r1:SAAS-3")] == ["FAILED", "PAID"]


def test_already_paid_counts_only_with_a_settlement_from_this_agent(key_file, tmp_path):
    seller = FakeResponse(200, {"invoice": "TS-10443", "status": "paid", "transaction": TX})
    someone_else = FakeHttp([seller], settlements=[[settlement(payer=OTHER)]])
    with pytest.raises(MetalError):
        pay_once(rail(key_file, FakeCli(), someone_else), Ledger(tmp_path / "a.json"), "metal:r1:SAAS-3", URL,
                 "0.18", SUPPLIER, "ToolStack")
    ours = FakeHttp([seller], settlements=[[settlement()]])
    paid = pay_once(rail(key_file, FakeCli(), ours), Ledger(tmp_path / "b.json"), "metal:r1:SAAS-3", URL, "0.18",
                    SUPPLIER, "ToolStack")
    assert (paid.status, paid.found_by) == ("PAID", "facilitator")


def test_balance_reads_the_chain_and_never_shows_the_rpc_url(key_file):
    with pytest.raises(NeedsRpc):
        rail(key_file, FakeCli()).balance()
    http = FakeHttp(rpc={"jsonrpc": "2.0", "id": 1, "result": hex(9994 * 10 ** 16)})
    assert rail(key_file, FakeCli(), http, rpc_url=RPC).balance() == Decimal("99.94")
    call = http.posts[0][1]["params"][0]
    assert call["to"] == USDZ and call["data"].endswith(AGENT[2:].lower())
    broken = FakeHttp(rpc={"jsonrpc": "2.0", "id": 1, "error": {"message": f"unknown endpoint {RPC}"}})
    with pytest.raises(MetalError) as e:
        rail(key_file, FakeCli(), broken, rpc_url=RPC).balance()
    assert RPC not in str(e.value) and "endpoint-id-that-is-a-credential" not in str(e.value)


def test_status_gets_the_rpc_through_the_environment_not_the_arguments(key_file):
    out = (f"transaction {TX}\nstatus success\nblock 9735920\nsubmitter 0xA1EA44AfE3948831194C90438F23827c8b458F88\n"
           f"transfer 0.18 *USDZ from {AGENT} to {SUPPLIER}\n")
    cli = FakeCli((0, out, ""))
    state = rail(key_file, cli, rpc_url=RPC).payment_status(TX)
    assert (state["source"], state["status"], state["block"]) == ("chain", "success", "9735920")
    assert RPC not in " ".join(cli.calls[0]["argv"]) and cli.calls[0]["env"]["METAL_RPC_URL"] == RPC
    without = rail(key_file, FakeCli(), FakeHttp(settlements=[[settlement()]])).payment_status(TX, pay_to=SUPPLIER)
    assert (without["source"], without["status"], without["amount"]) == ("facilitator", "settled", Decimal("0.18"))
    with pytest.raises(NeedsRpc):
        rail(key_file, FakeCli()).payment_status(TX)


def test_pay_never_passes_an_rpc_url_to_the_cli(key_file, monkeypatch):
    monkeypatch.setenv("METAL_RPC_URL", RPC)
    cli = FakeCli((0, PAID_OUT, ""))
    rail(key_file, cli, rpc_url=RPC).pay(URL, "0.18", SUPPLIER)
    assert "METAL_RPC_URL" not in cli.calls[0]["env"]


def test_fund_reads_the_grant_and_an_address_already_funded(key_file):
    grant = (f"funded {AGENT} with 100 *USDZ from the testnet faucet\ntransaction {TX}\n"
             f"explorer https://testnet.metalntwx.com/tx/{TX}\n")
    got = rail(key_file, FakeCli((0, grant, ""))).fund()
    assert (got["funded"], got["amount"], got["transaction"]) == (True, Decimal("100"), TX)
    again = rail(key_file, FakeCli((0, f"{AGENT} already holds a full *USDZ grant from the testnet faucet\n", ""))).fund()
    assert again["already_funded"] and not again["funded"]
    limited = "error: faucet answered 429 client_rate_limited: this client asked the faucet too recently; retry in 30 s\n"
    with pytest.raises(MetalError) as e:
        rail(key_file, FakeCli((1, "", limited))).fund()
    assert "retry in 30 s" in str(e.value)


def test_amounts_are_cents_of_usdz(key_file):
    for bad in ("0", "-1", "0.001", "abc"):
        with pytest.raises(ValueError):
            rail(key_file, FakeCli()).pay(URL, bad, SUPPLIER)


# ---------- under a mandate (the owner's limits, enforced by Metal) ----------
OWNER = "0x2222222222222222222222222222222222222222"


@pytest.fixture
def mandate(tmp_path):
    """A credential shaped like metal-agent's: layer 1 is an SD-JWT whose `sub` is the owner's Metal account."""
    part = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    layer1 = f"{part({'alg': 'ES256'})}.{part({'sub': OWNER, 'vct': 'metal'})}.c2lnbmF0dXJl~"
    path = tmp_path / "mandato.json"
    path.write_text(json.dumps({"format": "verifiable-intent", "version": "0.1", "layer1": layer1,
                                "layer2": "x.y.z"}), encoding="utf-8")
    return path


def test_under_a_mandate_the_cli_gets_the_credential_and_no_key_or_limit(key_file, mandate):
    out = PAID_OUT.replace(f"payer {AGENT}", f"payer {OWNER}")
    cli = FakeCli((0, f"mandate 0xref\n{out}", ""))
    r = MetalRail(key_file, cli=["node", "metal-agent.js"], run=cli, http=FakeHttp(), mandate=mandate,
                  sleep=lambda s: None)
    paid = r.pay(URL, "0.18", SUPPLIER)
    argv = cli.calls[0]["argv"]
    assert argv[argv.index("--mandate") + 1] == str(mandate) and "--key" not in argv and "--max" not in argv
    assert (paid.status, paid.payer, r.address()) == ("PAID", OWNER, OWNER)
    assert mandate_principal(mandate) == OWNER and "mandate=" in repr(r)


def test_under_a_mandate_a_doubt_is_looked_up_with_the_owner_as_payer(key_file, mandate):
    cli = FakeCli((1, f"nonce {NONCE}\n", IN_DOUBT_ERR))
    http = FakeHttp(settlements=[[settlement(payer=AGENT), settlement(payer=OWNER)]])
    paid = MetalRail(key_file, cli=["node", "metal-agent.js"], run=cli, http=http, mandate=mandate,
                     sleep=lambda s: None).pay(URL, "0.18", SUPPLIER)
    assert (paid.found_by, paid.payer) == ("facilitator", OWNER)


def test_a_payment_the_mandate_refuses_moved_nothing(key_file, mandate):
    err = ("error: payment refused: mandate_cap_exceeded (the payment is above the mandate's cap) "
           "(offer: 0.18 *USDZ on eip155:63825); nothing was paid\n")
    with pytest.raises(PaymentRefused) as e:
        MetalRail(None, cli=["node", "metal-agent.js"], run=FakeCli((1, f"nonce {NONCE}\n", err)), http=FakeHttp(),
                  mandate=mandate, sleep=lambda s: None).pay(URL, "0.18", SUPPLIER)
    assert e.value.reason == "mandate_cap_exceeded"


def test_a_file_that_is_not_a_credential_is_refused(tmp_path):
    bad = tmp_path / "x.json"
    bad.write_text("{}")
    with pytest.raises(MetalError):
        mandate_principal(bad)
    with pytest.raises(ValueError):
        MetalRail(None)
