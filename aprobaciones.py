"""Aprobaciones de conversiones atadas a una cotización de Airwallex.

Cuando una conversión supera lo que el agente puede hacer solo:
1. Pide a Airwallex una cotización: el tipo queda fijo durante 1 hora y se sabe el coste exacto en dólares
2. Crea una tarjeta con lo que se aprueba: importe exacto, divisa, contraparte, motivo y señales (el correo)
3. Una persona la aprueba o la rechaza (`aprobar.py`)
4. En la siguiente ejecución, si la tarjeta está aprobada y la cotización sigue vigente, se convierte a ese tipo

Si cambia algo (el importe, la ronda, las señales o la cotización), la aprobación vieja no vale y sale otra tarjeta.

Otra tarjeta, sin cotización: pagar con la reserva el pago que para la empresa (28-oct). El agente nunca baja de la
reserva solo; si no tocarla obliga a aplazar ese pago, pide permiso para pagarlo con ella.
"""
from __future__ import annotations

from datetime import datetime, timezone

from escenario import BASE, DATA  # añade motor_comun al path
from triage_core.approvals import ApprovalBook, ApprovalRequest

APROBACIONES = DATA / "aprobaciones.json"
VALIDEZ = "HR_1"
CADUCADA = "quote expired"  # una tarjeta pendiente cuya cotización venció no es un «no» de una persona

APROBADA, ESPERA, RECHAZADA = "APROBADA", "ESPERA", "RECHAZADA"
CON_RESERVA = "pay_from_reserve"


def contraparte(buy_amount: float, buy_currency: str) -> str:
    return f"Airwallex FX: buy {buy_amount:.2f} {buy_currency}"


def _hasta(quote: dict) -> datetime:
    return datetime.fromisoformat(quote["valid_to_at"].replace("Z", "+00:00"))


def vigente(quote: dict, ahora: datetime) -> bool:
    return _hasta(quote) > ahora


def _cotizacion(req: ApprovalRequest) -> str:
    return req.evidence[0].removeprefix("quote:")


def titulo(req: ApprovalRequest) -> str:
    """Lo que se aprueba, en una línea (consola y pantalla)."""
    importe = f"${req.amount:,.2f}" if req.currency == "USD" else f"{req.amount:,.2f} {req.currency}"
    if req.action == CON_RESERVA:
        return f"Pay {importe} to {req.counterparty} from the cash reserve"
    cantidad, divisa = req.counterparty.removeprefix("Airwallex FX: buy ").split()
    simbolo = {"EUR": "€", "GBP": "£"}.get(divisa)
    compra = f"{simbolo}{float(cantidad):,.2f}" if simbolo else f"{float(cantidad):,.2f} {divisa}"
    return f"Buy {compra} for {importe}"


def tarjeta(fp: str, req: ApprovalRequest, quote: dict | None = None) -> str:
    lineas = [f"🟡 APPROVAL NEEDED [{fp[:8]}]", f"   {titulo(req)}"]
    if quote:
        lineas[-1] += f" (rate {quote['client_rate']} locked until {_hasta(quote).astimezone():%H:%M})"
    lineas.append(f"   Why: {req.reason}")
    lineas += [f"   Evidence: {e}" for e in req.evidence[2:]]
    lineas.append(f"   To approve:  python aprobar.py {fp[:8]}   (or --rechazar)")
    return "\n".join(lineas)


def cotizacion(req: ApprovalRequest) -> str:
    """El quote_id que ata esta tarjeta (para enseñarlo en la pantalla)."""
    return _cotizacion(req)


def cotizacion_aprobada(rail, book: ApprovalBook, buy_currency: str, buy_amount: float, ronda: str,
                        ahora: datetime | None = None) -> str | None:
    """La cotización de una tarjeta aprobada para esta misma conversión y ronda, si sigue vigente.

    No mira las señales: sirve cuando el agente ya no necesita aprobación (las señales cambiaron a mejor)
    pero una persona aprobó un coste exacto, y es mejor cumplir lo aprobado que convertir a mercado."""
    ahora = ahora or datetime.now(timezone.utc)
    cp = contraparte(buy_amount, buy_currency)
    for g in book.granted.values():
        req = g["request"]
        if req.counterparty == cp and req.evidence[1:2] == (f"ronda:{ronda}",):
            q = rail.get_quote(_cotizacion(req))
            if vigente(q, ahora) and round(float(q["sell_amount"]), 2) == round(req.amount, 2):
                return q["quote_id"]
    return None


def aprobacion_de_conversion(rail, book: ApprovalBook, sell_currency: str, buy_currency: str,
                             buy_amount: float, motivo: str, ronda: str, senales: tuple[str, ...],
                             ahora: datetime | None = None, log=print) -> tuple[str, str]:
    """Devuelve (APROBADA, quote_id) si se puede convertir ya, o (ESPERA | RECHAZADA, huella de la tarjeta)."""
    ahora = ahora or datetime.now(timezone.utc)
    cp, contexto = contraparte(buy_amount, buy_currency), (f"ronda:{ronda}", *senales)
    misma = lambda r: r.counterparty == cp and r.evidence[1:] == contexto

    for fp, g in book.granted.items():
        if misma(g["request"]):
            q = rail.get_quote(_cotizacion(g["request"]))
            if vigente(q, ahora) and round(float(q["sell_amount"]), 2) == round(g["request"].amount, 2):
                return APROBADA, q["quote_id"]
    for fp, r in book.rejected.items():
        if misma(r["request"]) and r["approver"] != CADUCADA:
            return RECHAZADA, fp
    for fp, req in list(book.pending.items()):
        if misma(req):
            if vigente(rail.get_quote(_cotizacion(req)), ahora):
                return ESPERA, fp
            book.reject(fp, CADUCADA)

    q = rail.create_quote(sell_currency, buy_currency, buy_amount, VALIDEZ)
    req = ApprovalRequest("fx_conversion", float(q["sell_amount"]), sell_currency, cp, motivo,
                          (f"quote:{q['quote_id']}", *contexto))
    fp = book.request(req)
    log(tarjeta(fp, req, q))
    return ESPERA, fp


def aprobacion_de_reserva(book: ApprovalBook, payee: str, amount: float, currency: str, motivo: str, ronda: str,
                          senales: tuple[str, ...], log=print) -> tuple[str, str]:
    """Devuelve (APROBADA | ESPERA | RECHAZADA, huella de la tarjeta). Sin aprobación, el pago no sale."""
    req = ApprovalRequest(CON_RESERVA, round(amount, 2), currency, payee, motivo, ("reserve", f"ronda:{ronda}", *senales))
    fp = req.fingerprint
    if fp in book.granted:
        return APROBADA, fp
    if fp in book.rejected:
        return RECHAZADA, fp
    if fp not in book.pending:
        book.request(req)
        log(tarjeta(fp, req))
    return ESPERA, fp
