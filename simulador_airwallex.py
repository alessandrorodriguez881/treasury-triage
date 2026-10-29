"""Simulador del sandbox de Airwallex para el modo demo: las mismas llamadas REST que usa el proyecto, sin red.

Hace de «sesión HTTP» debajo de `AirwallexRail`, así que la demo pasa por el mismo código que con el sandbox
de verdad (login, request_id, reintentos, libro, aprobaciones). Lo que imita, visto en el sandbox real el 27-oct:
- Saldos por divisa; un depósito simulado responde PENDING pero el dinero entra al momento
- Transferencias en SCHEDULED que se mueven con la simulación a SENT y PAID; si fallan, se devuelve el importe,
  pero Airwallex se queda la comisión. Comisiones por red las de `PAYOUT_FEES` (medidas en el sandbox)
- Conversiones SETTLED al momento; un request_id repetido se rechaza. Cotizaciones con el tipo fijo 1 hora,
  algo peores que el indicativo
El estado se guarda en un JSON tras cada escritura y se relee en cada llamada: la pantalla y la consola ven lo mismo.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import threading
from urllib.parse import urlparse
import uuid

import escenario  # noqa: F401  (añade motor_comun al path)
from triage_core.policy import PAYOUT_FEES

TIPOS = {"USD": 1.0, "EUR": 1.1265, "GBP": 1.346}  # USD por unidad, nivel CLIENT del sandbox el 27-oct
RECARGO_COTIZACION = 0.0012                        # 1,1279 frente a 1,1265 en el sandbox
ORDEN = ["SCHEDULED", "PROCESSING", "SENT", "PAID"]

# En la pantalla, una sola capa (y un solo simulador) atiende a todos los visitantes, cada uno en su carpeta: las
# llamadas van de una en una, para que una no cargue el estado de un visitante mientras otra guarda el de otro.
_CANDADO = threading.Lock()


class _Error(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class _Respuesta:
    """Lo poco de `requests.Response` que usa la capa."""

    def __init__(self, status: int, data=None):
        self.status_code, self._data = status, data
        self.ok = 200 <= status < 300
        self.content = b"" if data is None else json.dumps(data).encode()
        self.text = self.content.decode()

    def json(self):
        return self._data


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


class SandboxSimulado:
    VACIO = {"saldos": {}, "cuentas": [], "beneficiarios": [], "transferencias": {}, "conversiones": {},
             "cotizaciones": {}, "depositos": []}

    def __init__(self, ruta: Path | None = None):
        self.ruta = ruta
        self.estado = json.loads(json.dumps(self.VACIO))

    # ---------- la parte de requests.Session que usa AirwallexRail ----------
    def post(self, url, headers=None, timeout=None, **kw):  # solo el login
        return _Respuesta(200, {"token": "simulated-token", "expires_at": (_ahora() + timedelta(minutes=30)).isoformat()})

    def request(self, method, url, json=None, params=None, headers=None, timeout=None):
        with _CANDADO:
            return self._atender(method, url, json, params)

    def _atender(self, method, url, json, params):
        self._cargar()
        try:
            datos = self._despachar(method, urlparse(url).path.removeprefix("/api/v1/"), json or {}, params or {})
        except _Error as e:
            return _Respuesta(e.status, {"code": e.code, "message": e.message})
        if method != "GET":  # céntimos exactos, como Airwallex (sin restos de coma flotante)
            self.estado["saldos"] = {d: round(v, 2) for d, v in self.estado["saldos"].items()}
            self._guardar()
        return _Respuesta(200, datos)

    # ---------- estado en disco ----------
    def _cargar(self) -> None:
        if self.ruta and self.ruta.exists():
            self.estado = json.loads(self.ruta.read_text(encoding="utf-8"))
        elif self.ruta:  # visitante nuevo: empieza vacío, sin heredar lo que dejó en memoria el anterior
            self.estado = json.loads(json.dumps(self.VACIO))

    def _guardar(self) -> None:
        if self.ruta:
            self.ruta.parent.mkdir(parents=True, exist_ok=True)
            self.ruta.write_text(json.dumps(self.estado, indent=1), encoding="utf-8")

    # ---------- los endpoints ----------
    def _despachar(self, method: str, p: str, body: dict, params: dict):
        e = self.estado
        if method == "GET":
            if p == "global_accounts":
                return {"items": e["cuentas"]}
            if p == "balances/current":
                return [{"currency": d, "available_amount": round(v, 2), "pending_amount": 0, "reserved_amount": 0,
                         "total_amount": round(v, 2)} for d, v in sorted(e["saldos"].items())]
            if p == "beneficiaries":
                return {"items": e["beneficiarios"]}
            if p in ("transfers", "fx/conversions"):
                tabla = e["transferencias"] if p == "transfers" else e["conversiones"]
                return {"items": [x for x in tabla.values() if x["request_id"] == params.get("request_id")]}
            if p.startswith("transfers/"):
                return self._uno(e["transferencias"], p.split("/")[1], "transfer")
            if p.startswith("fx/conversions/"):
                return self._uno(e["conversiones"], p.split("/")[2], "conversion")
            if p.startswith("fx/quotes/"):
                return self._uno(e["cotizaciones"], p.split("/")[2], "quote")
            if p == "fx/rates/current":
                return self._tipo(params["buy_currency"], params["sell_currency"], float(params.get("buy_amount", 1)))
            if p == "deposits":
                return self._depositos(params)
        if method == "POST":
            if p == "global_accounts/create":
                return self._cuenta(body)
            if p == "simulation/deposit/create":
                return self._deposito(body)
            if p == "beneficiaries/create":
                return self._beneficiario(body)
            if p == "transfers/create":
                return self._transferencia(body)
            if p.startswith("simulation/transfers/") and p.endswith("/transition"):
                return self._transicion(p.split("/")[2], body["next_status"])
            if p == "fx/quotes/create":
                return self._cotizacion(body)
            if p == "fx/conversions/create":
                return self._conversion(body)
        raise _Error(404, "resource_not_found", f"the simulator does not implement {method} /api/v1/{p}")

    @staticmethod
    def _uno(tabla: dict, id_: str, que: str) -> dict:
        if id_ not in tabla:
            raise _Error(404, "resource_not_found", f"{que} {id_} not found")
        return tabla[id_]

    def _cuenta(self, body: dict) -> dict:
        rasgos = body["required_features"]
        cuenta = {"id": str(uuid.uuid4()), "nick_name": body.get("nick_name"), "country_code": body["country_code"],
                  "currency": rasgos[0]["currency"], "status": "ACTIVE", "supported_features": rasgos}
        self.estado["cuentas"].append(cuenta)
        self.estado["saldos"].setdefault(cuenta["currency"], 0.0)
        return cuenta

    def _deposito(self, body: dict) -> dict:
        cuenta = next((c for c in self.estado["cuentas"] if c["id"] == body["global_account_id"]), None)
        if cuenta is None:
            raise _Error(400, "invalid_argument", "global_account_id not found")
        saldos = self.estado["saldos"]
        saldos[cuenta["currency"]] = saldos.get(cuenta["currency"], 0.0) + float(body["amount"])
        dep = {"id": str(uuid.uuid4()), "status": "PENDING", "amount": float(body["amount"]),
               "currency": cuenta["currency"], "reference": body.get("reference")}
        # en la lista de depósitos sale ya SETTLED, con el pagador y la hora (como en el sandbox)
        self.estado["depositos"].append({**dep, "status": "SETTLED", "global_account_id": cuenta["id"],
                                         "payer": {"name": body.get("payer_name", "")},
                                         "created_at": _ahora().strftime("%Y-%m-%dT%H:%M:%S+0000")})
        return dep

    def _depositos(self, params: dict) -> dict:
        """GET /deposits: los de desde `from_created_at`, por páginas (el sandbox pide page_num y page_size)."""
        if "page_num" not in params or "page_size" not in params:
            raise _Error(400, "invalid_argument", "both page_num and page_size need to be filled")
        desde = datetime.fromisoformat(params["from_created_at"].replace("Z", "+00:00")) \
            if params.get("from_created_at") else None
        todos = [d for d in self.estado["depositos"] if "created_at" in d
                 and (desde is None or datetime.fromisoformat(d["created_at"]) >= desde)]
        tam, pag = int(params["page_size"]), int(params["page_num"])
        return {"items": todos[pag * tam:(pag + 1) * tam], "has_more": len(todos) > (pag + 1) * tam,
                "total_count": len(todos)}

    def _beneficiario(self, body: dict) -> dict:
        if not body.get("beneficiary", {}).get("company_name"):
            raise _Error(400, "validation_failed", "beneficiary.company_name is required")
        id_ = str(uuid.uuid4())
        b = {"id": id_, "beneficiary_id": id_, "nickname": body["nickname"], "beneficiary": body["beneficiary"],
             "transfer_methods": body.get("transfer_methods", ["LOCAL"])}
        self.estado["beneficiarios"].append(b)
        return b

    def _transferencia(self, body: dict) -> dict:
        if any(t["request_id"] == body["request_id"] for t in self.estado["transferencias"].values()):
            raise _Error(400, "duplicate_request", "request_id already used")
        if not any(b["id"] == body["beneficiary_id"] for b in self.estado["beneficiarios"]):
            raise _Error(400, "invalid_argument", "beneficiary_id not found")
        divisa, importe = body["transfer_currency"], float(body["transfer_amount"])
        comision = PAYOUT_FEES.get((body.get("transfer_method", "LOCAL"), divisa), 0.0) \
            if body.get("fee_paid_by", "PAYER") == "PAYER" else 0.0
        if self.estado["saldos"].get(divisa, 0.0) < importe + comision - 1e-9:
            raise _Error(400, "balance_insufficient", f"not enough {divisa} for {importe:.2f} plus the fee")
        self.estado["saldos"][divisa] -= importe + comision
        t = {"id": str(uuid.uuid4()), "request_id": body["request_id"], "status": "SCHEDULED",
             "beneficiary_id": body["beneficiary_id"], "transfer_currency": divisa, "transfer_amount": importe,
             "transfer_method": body.get("transfer_method", "LOCAL"), "fee_amount": comision,
             "reference": body.get("reference"), "created_at": _ahora().isoformat()}
        self.estado["transferencias"][t["id"]] = t
        return t

    def _transicion(self, id_: str, siguiente: str) -> dict:
        t = self._uno(self.estado["transferencias"], id_, "transfer")
        if siguiente in ("FAILED", "CANCELLED"):
            if t["status"] not in ("FAILED", "CANCELLED", "PAID"):
                self.estado["saldos"][t["transfer_currency"]] += t["transfer_amount"]  # la comisión no vuelve
            t["status"] = siguiente
        elif siguiente in ORDEN and t["status"] in ORDEN and ORDEN.index(siguiente) > ORDEN.index(t["status"]):
            t["status"] = siguiente
        else:
            raise _Error(400, "invalid_status_transition", f"cannot move a {t['status']} transfer to {siguiente}")
        return t

    @staticmethod
    def _tipo(compra: str, venta: str, cantidad: float) -> dict:
        tipo = TIPOS[compra] / TIPOS[venta]
        return {"buy_currency": compra, "sell_currency": venta, "client_rate": round(tipo, 6),
                "rate_details": [{"level": "CLIENT", "buy_amount": cantidad, "sell_amount": round(cantidad * tipo, 2),
                                  "rate": round(tipo, 6)}]}

    def _cotizacion(self, body: dict) -> dict:
        compra, venta, cantidad = body["buy_currency"], body["sell_currency"], float(body["buy_amount"])
        tipo = TIPOS[compra] / TIPOS[venta] * (1 + RECARGO_COTIZACION)
        horas = {"HR_1": 1, "HR_4": 4, "HR_8": 8, "HR_24": 24}.get(body.get("validity", "HR_1"), 1)
        q = {"quote_id": str(uuid.uuid4()), "buy_currency": compra, "sell_currency": venta, "buy_amount": cantidad,
             "sell_amount": round(cantidad * tipo, 2), "client_rate": round(tipo, 6),
             "valid_from_at": _ahora().isoformat(), "valid_to_at": (_ahora() + timedelta(hours=horas)).isoformat()}
        self.estado["cotizaciones"][q["quote_id"]] = q
        return q

    def _conversion(self, body: dict) -> dict:
        if any(c["request_id"] == body["request_id"] for c in self.estado["conversiones"].values()):
            raise _Error(400, "duplicate_request", "request_id already used")
        compra, venta, cantidad = body["buy_currency"], body["sell_currency"], float(body["buy_amount"])
        if body.get("quote_id"):
            q = self._uno(self.estado["cotizaciones"], body["quote_id"], "quote")
            if datetime.fromisoformat(q["valid_to_at"]) <= _ahora():
                raise _Error(400, "quote_expired", "the quote has expired")
            if round(q["buy_amount"], 2) != round(cantidad, 2):
                raise _Error(400, "invalid_argument", "buy_amount does not match the quote")
            precio, tipo = q["sell_amount"], q["client_rate"]
        else:
            tipo = TIPOS[compra] / TIPOS[venta]
            precio = round(cantidad * tipo, 2)
        saldos = self.estado["saldos"]
        if saldos.get(venta, 0.0) < precio - 1e-9:
            raise _Error(400, "balance_insufficient", f"not enough {venta} to buy {cantidad:.2f} {compra}")
        saldos[venta] -= precio
        saldos[compra] = saldos.get(compra, 0.0) + cantidad
        id_ = str(uuid.uuid4())
        c = {"id": id_, "conversion_id": id_, "request_id": body["request_id"], "status": "SETTLED",
             "buy_currency": compra, "sell_currency": venta, "buy_amount": cantidad, "sell_amount": precio,
             "client_rate": round(tipo, 6), "quote_id": body.get("quote_id")}
        self.estado["conversiones"][id_] = c
        return c
