#!/usr/bin/env node
// Payee Check: a DEMO service we run locally, sold over x402 on Metal testnet (0.25 *USDZ per check).
//
// It answers like the EU's Verification of Payee: is this account registered to this name? match, close_match
// (with the name on record), no_match, or not_possible (account unknown). Its registry is synthetic: the four
// demo suppliers' accounts, and the account in Q. Consulting's invoice, registered to someone else.
//
// Each check id is paid ONCE. An id that is already paid answers with its stored result and transaction, and asks
// for no payment, so a retry never pays twice. While one payment for an id is settling, a second one gets 409.
//
//   node verificador_x402.mjs --pay-to <address> --state <file.json> --registry <registro_demo.json> [--port 4022]
//
//   GET /health                               who gets paid, on which network, the price
//   GET /checks/:id?account=<acct>&name=<n>   x402: 402 with the price, then the result with its transaction
//
// The service holds no key: it only names the address it is paid to. Metal's facilitator pays the gas.
import { readFileSync, renameSync, writeFileSync, existsSync } from "node:fs";
import { parseArgs } from "node:util";
import { serve } from "@hono/node-server";
import { Hono } from "hono";
import { paymentMiddleware } from "@x402/hono";
import { HTTPFacilitatorClient, x402ResourceServer } from "@x402/core/server";
import { decodePaymentResponseHeader } from "@x402/core/http";
import { METAL_NETWORKS, registerMetalScheme } from "@metalntwx/metal-pay";

const SERVICE = "Payee Check (demo service run locally)";
const { values } = parseArgs({
  options: {
    "pay-to": { type: "string" },
    state: { type: "string" },
    registry: { type: "string" },
    price: { type: "string", default: "0.25" },
    port: { type: "string", default: "4022" },
    host: { type: "string", default: "127.0.0.1" },
    network: { type: "string", default: "testnet" },
  },
});

const network = METAL_NETWORKS[values.network];
if (!network) throw new Error(`--network must be devnet or testnet, not ${values.network}`);
const payTo = values["pay-to"];
if (!/^0x[0-9a-fA-F]{40}$/.test(payTo ?? "")) throw new Error("--pay-to must be the service's 0x address");
if (!values.state || !values.registry) throw new Error("--state and --registry name the service's files");
if (!/^\d+(\.\d{1,2})?$/.test(values.price)) throw new Error("--price is *USDZ to the cent, such as 0.25");

const compact = (account) => String(account ?? "").replace(/[\s-]/g, "").toUpperCase();
const registry = Object.fromEntries(Object.entries(JSON.parse(readFileSync(values.registry, "utf8")).accounts)
  .map(([account, holder]) => [compact(account), holder]));

// Names compared as a bank would, roughly: case, punctuation, "&" and company suffixes do not count
const SUFFIXES = new Set(["inc", "ltd", "llc", "gmbh", "uab", "co", "corp", "company", "limited", "plc", "sa", "the"]);
const words = (name) => String(name ?? "").toLowerCase().replace(/&/g, " and ").replace(/[^a-z0-9]+/g, " ")
  .split(" ").filter((w) => w && !SUFFIXES.has(w));
function verify(account, name) {
  const holder = registry[compact(account)];
  if (!holder) return { result: "not_possible", name_on_record: null };
  const [asked, held] = [words(name), words(holder)];
  if (asked.join(" ") === held.join(" ")) return { result: "match", name_on_record: null };
  const shared = asked.filter((w) => held.includes(w)).length;
  const close = shared > 0 && shared >= Math.min(asked.length, held.length);
  // Like VoP: a close match tells the name on record; a no match does not
  return close ? { result: "close_match", name_on_record: holder } : { result: "no_match", name_on_record: null };
}

// Paid checks survive a restart: written whole, then renamed into place
const state = existsSync(values.state) ? JSON.parse(readFileSync(values.state, "utf8")) : {};
const save = () => {
  writeFileSync(`${values.state}.tmp`, JSON.stringify(state, null, 2));
  renameSync(`${values.state}.tmp`, values.state);
};

const facilitator = new HTTPFacilitatorClient({ url: network.facilitatorUrl });
const server = registerMetalScheme(new x402ResourceServer(facilitator), { networks: [network.name] });
const routes = {
  "GET /checks/:id": {
    accepts: { scheme: "exact", network: network.network, payTo, price: values.price, maxTimeoutSeconds: 60 },
    description: "Payee check: is this account registered to this name? (demo service, synthetic registry)",
    mimeType: "application/json",
  },
};

const inFlight = new Set();
const app = new Hono();
app.get("/health", (c) => c.json({ ok: true, service: SERVICE, pay_to: payTo, network: network.network,
  price: values.price, currency: "*USDZ" }));

// Before x402: a bad request, a paid check or one that is settling never reaches the payment check
app.use("/checks/:id", async (c, next) => {
  const id = c.req.param("id");
  const account = c.req.query("account");
  const name = c.req.query("name");
  if (!/^[A-Za-z0-9._-]{1,80}$/.test(id) || !account || !name) {
    return c.json({ error: "a check needs an id ([A-Za-z0-9._-]), ?account= and ?name=" }, 400);
  }
  if (state[id]) return c.json(state[id], 200, { "x-check-status": "already_paid" });
  const paying = Boolean(c.req.header("payment-signature") || c.req.header("x-payment"));
  if (paying && inFlight.has(id)) return c.json({ error: "another payment for this check is settling" }, 409);
  if (paying) inFlight.add(id);
  try {
    await next();
  } finally {
    if (paying) inFlight.delete(id);
  }
  const receipt = c.res.headers.get("payment-response");
  const settled = receipt ? decodePaymentResponseHeader(receipt) : undefined;
  if (!settled?.success) return;
  state[id] = {
    check_id: id, service: SERVICE, account: compact(account), name, ...verify(account, name),
    checked_at: new Date().toISOString(), status: "paid", price: values.price, currency: "*USDZ",
    network: network.network, pay_to: payTo, transaction: settled.transaction, payer: settled.payer,
    settlement_id: settled.extra?.settlement?.id ?? null,
  };
  save();
  console.log(`check ${id}: ${state[id].result} for ${name}, paid by ${settled.payer} in ${settled.transaction}`);
  const headers = new Headers(c.res.headers);
  headers.delete("content-length");
  c.res = undefined; // Hono merges the old response's headers into a new one unless it is cleared first
  c.res = new Response(JSON.stringify(state[id]), { status: 200, headers });
});

app.use(paymentMiddleware(routes, server));
app.get("/checks/:id", (c) => c.json({ check_id: c.req.param("id"), status: "settling" }));

serve({ fetch: app.fetch, port: Number(values.port), hostname: values.host }, (info) => {
  console.log(`${SERVICE} on http://${values.host}:${info.port}, ${values.price} *USDZ per check, paid to ${payTo}`);
});
