"""Comprobación del beneficiario en Metal (red de pruebas), para `demo.comprobar_beneficiarios`.

Antes de que una persona decida sobre una factura que pide pagar a una cuenta que no está en la ficha del proveedor
(en la demo, la de Q. Consulting), el agente compra una comprobación de esa cuenta, como la «Verification of Payee»
de la UE: ¿está a nombre de quien dice? La paga con 0,25 *USDZ por x402 a «Payee Check», un servicio de demostración
que corre en local (metal/verificador_x402.mjs, con un registro sintético). Es una prueba más para quien decide: no
cambia ninguna decisión (NEW-5 va a una persona igual), ni el plan, ni ninguna cifra.

Modos (`modo_metal`):
- real: paga de verdad en Metal testnet. Necesita lo que instala `metal/preparar.py` y la clave del agente
  (data/metal/agente.key.json). Por defecto solo con el sandbox de Airwallex de verdad; TT_METAL=real lo fuerza
- replay: repite una comprobación REAL grabada (metal/grabacion_metal.json, con su transacción y su enlace)
- off: TT_METAL=off
Con TT_GRABAR_METAL=1, una comprobación real se graba para la demo sin Metal (como TT_GRABAR=1 con Claude).
Con data/metal/mandato.json, el agente paga bajo el mandato del dueño (límites que cumple la propia red).

El pago va a su propio libro (data/metal/libro_metal.json, con su registro encadenado), no al de Airwallex: así la
pestaña de auditoría y sus cifras no cambian. Nunca imprime claves ni la URL del RPC.
"""
from __future__ import annotations

import atexit
from datetime import date
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from urllib.parse import quote

import requests

from escenario import DATA, EN_FICHA, OBLIGATIONS  # añade motor_comun al path
from modo import SIMULADO
from rail_metal import EXPLORER, RUNTIME, MetalError, MetalRail, pay_once
from triage_core.ledger import DuplicatePaymentError, Ledger

APP = Path(__file__).parent
METAL = APP / "metal"
DATOS_METAL = APP / "data" / "metal"  # fuera de los repositorios (data/)
CLAVE_AGENTE = DATOS_METAL / "agente.key.json"
MANDATO = DATOS_METAL / "mandato.json"
LIBRO_METAL = DATOS_METAL / "libro_metal.json"
GRABACION_METAL = METAL / "grabacion_metal.json"
GRABAR_METAL = os.environ.get("TT_GRABAR_METAL") == "1"  # la grabación del repositorio solo se cambia a propósito
SERVICIO = json.loads((METAL / "verificador.json").read_text(encoding="utf-8"))
REPLAY = "replay of a real Metal testnet payment"
RESULTADOS = {
    "match": "the account in this invoice is registered to {payee}",
    "close_match": "the account in this invoice is registered to {name_on_record}, close to {payee}",
    "no_match": "the account in this invoice is not registered to {payee}",
    "not_possible": "the account in this invoice could not be checked",
}


def _compact(cuenta: str) -> str:
    return re.sub(r"[\s\-]", "", cuenta or "").upper()


def modo_metal() -> str:
    """real, replay u off (ver arriba)."""
    pedido = os.environ.get("TT_METAL", "").strip().lower()
    if pedido in ("off", "replay", "real"):
        return pedido
    listo = (RUNTIME / "node_modules").exists() and (CLAVE_AGENTE.exists() or MANDATO.exists())
    return "real" if listo and not SIMULADO else "replay"


def por_comprobar(facturas) -> list[tuple]:
    """(lectura, beneficiario) de cada factura que pide pagar a una cuenta distinta de la de su ficha."""
    payee = {o.id: o.payee for o in OBLIGATIONS}
    return [(f, payee[f.obligation_id]) for f in facturas
            if f.obligation_id in payee and _compact(f.pay_to) != _compact(EN_FICHA.get(payee[f.obligation_id], ""))]


# ---------- el servicio local ----------
_PROCESO: subprocess.Popen | None = None


def _vivo(base: str) -> bool:
    try:
        r = requests.get(f"{base}/health", timeout=1)
        return r.ok and str(r.json().get("pay_to", "")).lower() == SERVICIO["pay_to"].lower()
    except (requests.RequestException, ValueError):
        return False


def _parar() -> None:
    if _PROCESO and _PROCESO.poll() is None:
        _PROCESO.terminate()


def asegurar_verificador(espera: float = 15.0) -> str:
    """La dirección del servicio Payee Check; lo arranca (desde la instalación local) si no está ya en marcha.
    La primera vez Node tarda unos 8 s en cargar: el panel lo arranca antes, con `precalentar`."""
    global _PROCESO
    base = f"http://127.0.0.1:{SERVICIO['port']}"
    if _vivo(base):
        return base
    if _PROCESO and _PROCESO.poll() is None:  # arrancado por `precalentar`: solo falta esperar
        return _esperar(base, espera)
    node = shutil.which("node")
    if not node or not (RUNTIME / "node_modules").exists():
        raise MetalError("the Metal runtime is not installed (python metal/preparar.py)")
    fuente = METAL / "verificador_x402.mjs"  # la de la app manda: se copia si cambió
    if not (RUNTIME / fuente.name).exists() or (RUNTIME / fuente.name).read_bytes() != fuente.read_bytes():
        shutil.copy2(fuente, RUNTIME / fuente.name)
    DATOS_METAL.mkdir(parents=True, exist_ok=True)
    salida = (DATOS_METAL / "verificador.log").open("a", encoding="utf-8")
    _PROCESO = subprocess.Popen(
        [node, fuente.name, "--pay-to", SERVICIO["pay_to"], "--price", SERVICIO["price"], "--port",
         str(SERVICIO["port"]), "--state", str(DATOS_METAL / "verificador_estado.json"),
         "--registry", str(METAL / "registro_demo.json")],
        cwd=RUNTIME, stdout=salida, stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    atexit.register(_parar)
    return _esperar(base, espera)


def _esperar(base: str, espera: float) -> str:
    limite = time.monotonic() + espera
    while espera > 0 and time.monotonic() < limite:
        if _vivo(base):
            return base
        if _PROCESO is not None and _PROCESO.poll() is not None:
            break
        time.sleep(0.3)
    if espera <= 0:
        return base
    raise MetalError("the payee check service did not start (see data/metal/verificador.log)")


def precalentar() -> None:
    """En modo real, arranca el servicio en segundo plano sin esperarlo (el paso 2 ya lo encuentra en marcha).
    Nunca falla: si no puede, el paso 2 lo intentará y, si tampoco, dejará su nota."""
    try:
        if modo_metal() == "real" and not (_PROCESO and _PROCESO.poll() is None):
            asegurar_verificador(espera=0)
    except Exception:  # noqa: BLE001 - el arranque en segundo plano nunca molesta a la pantalla
        pass


# ---------- una comprobación ----------
def _rail() -> MetalRail:
    # 10-nov: con 45 s, la herramienta de Metal no respondió a tiempo en 2 de 3 rondas (la red de pruebas iba lenta)
    return MetalRail(CLAVE_AGENTE if CLAVE_AGENTE.exists() else None, timeout=120, lookups=3,
                     mandate=MANDATO if MANDATO.exists() else None)


def resultado_base(f, payee: str, modo: str) -> dict:
    return {"obligation_id": f.obligation_id, "document_id": f.document_id, "payee": payee,
            "account": _compact(f.pay_to), "mode": modo}


def comprobar_real(ronda: str, f, payee: str, rail: MetalRail | None = None, libro: Ledger | None = None,
                   base: str | None = None) -> dict:
    """Paga la comprobación de esta factura en Metal (una vez por ronda y factura) y devuelve su resultado."""
    base = base or asegurar_verificador()
    rail = rail or _rail()
    libro = libro or Ledger(LIBRO_METAL)
    cuenta = _compact(f.pay_to)
    url = f"{base}/checks/{ronda}-{f.document_id}?account={quote(cuenta)}&name={quote(payee)}"
    try:
        pago = pay_once(rail, libro, f"check:{ronda}:{f.document_id}", url, SERVICIO["price"], SERVICIO["pay_to"],
                        SERVICIO["service"])
    except DuplicatePaymentError:  # ya pagada en esta ronda: el servicio da su resultado gratis
        pago = None
    cuerpo = pago.body if pago else None
    if not (isinstance(cuerpo, dict) and cuerpo.get("result")):
        _, cuerpo = rail.offer(url)  # una comprobación pagada contesta con su resultado guardado, sin cobrar
    if not isinstance(cuerpo, dict) or cuerpo.get("account") != cuenta or cuerpo.get("name") != payee \
            or cuerpo.get("result") not in RESULTADOS:
        raise MetalError("the payee check answered for another account or name")
    tx = (pago.transaction if pago else None) or cuerpo.get("transaction")
    return {**resultado_base(f, payee, "real"), "result": cuerpo["result"],
            "name_on_record": cuerpo.get("name_on_record"), "checked_at": cuerpo.get("checked_at"),
            "price": SERVICIO["price"], "transaction": tx, "explorer": f"{EXPLORER}/tx/{tx}" if tx else None,
            "payer": rail.address(), "service": SERVICIO["service"]}


GRABADO = ("payee", "account", "result", "name_on_record", "checked_at", "price", "transaction", "explorer", "payer",
           "service")


def grabar(r: dict) -> None:
    """Graba una comprobación real para repetirla sin Metal (solo a propósito: TT_GRABAR_METAL=1)."""
    datos = {"recorded_on": date.today().isoformat(),
             "what": "A real payee check paid on Metal testnet, replayed when the demo runs without Metal",
             **{k: r[k] for k in GRABADO}, "pay_to": SERVICIO["pay_to"], "network": "Metal testnet (eip155:63825)"}
    GRABACION_METAL.write_text(json.dumps(datos, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def comprobar_grabada(f, payee: str) -> dict:
    """La comprobación real grabada, si es de esta misma cuenta y este mismo nombre."""
    if not GRABACION_METAL.exists():
        raise MetalError("there is no recorded Metal payment to replay")
    g = json.loads(GRABACION_METAL.read_text(encoding="utf-8"))
    if g["account"] != _compact(f.pay_to) or g["payee"] != payee:
        raise MetalError("the recorded Metal payment checked another account")
    return {**resultado_base(f, payee, "replay"), **{k: g[k] for k in GRABADO if k not in ("payee", "account")},
            "recorded_on": g["recorded_on"]}


# ---------- lo guardado por ronda y lo que se enseña ----------
def _fichero(carpeta) -> Path:
    return Path(carpeta or DATA) / "comprobaciones.json"


def guardar_comprobaciones(ronda: str, resultados: list[dict], carpeta=None) -> None:
    fichero = _fichero(carpeta)
    fichero.parent.mkdir(parents=True, exist_ok=True)
    todas = json.loads(fichero.read_text(encoding="utf-8")) if fichero.exists() else {}
    todas[ronda] = resultados
    fichero.write_text(json.dumps(todas, indent=2, ensure_ascii=False), encoding="utf-8")


def comprobaciones_de(ronda: str, carpeta=None) -> dict[str, dict]:
    """Obligación -> la comprobación de su factura en esta ronda ({} si no hay, o si el fichero no se lee)."""
    try:
        return {r["obligation_id"]: r for r in json.loads(_fichero(carpeta).read_text(encoding="utf-8")).get(ronda, [])}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}


def _valida(r: dict) -> bool:
    return r.get("mode") in ("real", "replay") and r.get("result") in RESULTADOS


def texto(r: dict) -> str:
    """La línea de prueba (en inglés, para la pantalla y la consola). Lo que no se entienda sale como «unavailable»."""
    if not _valida(r):
        return f"Payee check on Metal unavailable ({r.get('note', 'no answer')}). The bill still goes to a person."
    que = RESULTADOS[r["result"]].format(payee=r.get("payee"), name_on_record=r.get("name_on_record"))
    resultado = r["result"].replace("_", " ")
    return f"Payee check on Metal: {resultado}. {que[:1].upper()}{que[1:]}. Paid {r.get('price')} *USDZ"


def texto_tarjeta(r: dict) -> str:
    """La línea de la tarjeta, en Markdown de Streamlit: con el enlace al explorador y, si es una repetición, dicho."""
    linea = texto(r).replace("*USDZ", "\\*USDZ").replace("$", "\\$")
    if not _valida(r):
        return f":material/policy: {linea}"
    if r["mode"] == "replay":
        linea += f" · {REPLAY} ({r.get('recorded_on', '')})"
    enlace = r.get("explorer") or ""
    return f":material/policy: {linea}" + (f" · [view on Metal explorer]({enlace})" if enlace.startswith(EXPLORER)
                                           else "")
