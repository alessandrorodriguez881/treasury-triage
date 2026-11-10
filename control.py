"""Lo que decide el dueño por encima del agente, en el mismo libro y con la misma auditoría encadenada (10-nov; mejora
3 del jurado adversario: «control humano que se vea»).

- **Pausar el agente.** Mientras está en pausa no mueve dinero ni lee correo: lo comprueban en código
  `demo.convertir`, `demo.pagar` y `demo.leer_correos` (no solo la pantalla). Reanudar lo deja seguir.
- **Rechazar un pago que el agente pasó a una persona** (en la demo, NEW-5, el proveedor nuevo). Sale del plan de la
  ronda, con su motivo.

Cada cosa es una entrada del libro (la pausa, con importe 0) y, por tanto, un evento de la auditoría encadenada:
`verificar_auditoria.py` la comprueba igual que los pagos. Las claves empiezan por `control:<ronda>:`, así que nada
de lo que busca pagos, conversiones o depósitos las confunde con dinero.
"""
from __future__ import annotations

from triage_core.ledger import Ledger

PAUSA, REANUDA, RECHAZO = "pause", "resume", "reject"
DUENO = "owner"


class AgentePausado(RuntimeError):
    pass


def _de_la_ronda(libro: Ledger, ronda: str) -> list:
    return [e for e in libro.entries if e.op_key.startswith(f"control:{ronda}:")]


def en_pausa(libro: Ledger, ronda: str) -> bool:
    """¿La última pausa o reanudación de la ronda es una pausa?"""
    marcas = [e for e in _de_la_ronda(libro, ronda) if e.kind in (PAUSA, REANUDA)]
    return bool(marcas) and marcas[-1].kind == PAUSA


def _apunta(libro: Ledger, ronda: str, que: str, importe: float = 0.0, divisa: str = "", quien: str = DUENO,
            estado: str = "RECORDED", clave: str | None = None) -> None:
    n = len(_de_la_ronda(libro, ronda)) + 1
    entrada = libro.begin(clave or f"control:{ronda}:{n}:{que}", que, importe, divisa, quien)
    libro.update(entrada, estado)


def pausar(libro: Ledger, ronda: str) -> bool:
    """Pausa el agente (si ya lo estaba, nada). Devuelve si ha cambiado algo."""
    if en_pausa(libro, ronda):
        return False
    _apunta(libro, ronda, PAUSA)
    return True


def reanudar(libro: Ledger, ronda: str) -> bool:
    if not en_pausa(libro, ronda):
        return False
    _apunta(libro, ronda, REANUDA)
    return True


def exige_en_marcha(libro: Ledger, ronda: str) -> None:
    """Para lo que mueve dinero o lee correo: con el agente en pausa, no se hace nada."""
    if en_pausa(libro, ronda):
        raise AgentePausado("the agent is paused by the owner: it moves no money and reads no email until you "
                            "resume it")


def rechazar(libro: Ledger, ronda: str, ob, motivo: str) -> bool:
    """El «no» del dueño a un pago que el agente le pasó, con su motivo (va en la contraparte del libro). Solo una vez
    por pago y ronda. Devuelve si ha cambiado algo."""
    clave = f"control:{ronda}:{RECHAZO}:{ob.id}"
    if libro.attempts(clave):
        return False
    _apunta(libro, ronda, RECHAZO, ob.amount, ob.currency, f"{ob.payee} · {motivo}", "REJECTED", clave)
    return True


def rechazados(libro: Ledger, ronda: str) -> dict[str, str]:
    """Lo que el dueño rechazó en esta ronda: id del pago → motivo."""
    out = {}
    for e in _de_la_ronda(libro, ronda):
        if e.kind == RECHAZO:
            out[e.op_key.rsplit(":", 1)[1]] = e.counterparty.split(" · ", 1)[1] if " · " in e.counterparty else ""
    return out
