"""Comprueba el registro de auditoría encadenado del libro de movimientos (motor común: `Ledger.verify`).

Cada cambio del libro se apunta también en `data/ledger.audit.jsonl` con la huella (hash) del anterior: si alguien
cambia, borra o reordena un apunte, o toca el libro a mano, aquí se ve. Los mensajes van en inglés (son del producto).

Uso:  python verificar_auditoria.py             la carpeta de datos de siempre
      python verificar_auditoria.py <carpeta>   otra: en el modo demo, la de un visitante (la pantalla dice cuál)
"""
from pathlib import Path
import sys

from escenario import DATA  # añade motor_comun al path
from triage_core.ledger import Ledger


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    libro = Ledger((Path(argv[0]) if argv else DATA) / "ledger.json")
    c = libro.verify()
    if not c.ok:
        print(f"PROBLEM in {libro.audit_path.name}: {c.problem}")
        return 1
    print(f"OK: {c.events} events, every hash chains to the one before, and {c.entries_checked} ledger entries "
          f"match their trail" + (f" ({c.before_trail} older entries predate the trail)" if c.before_trail else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
