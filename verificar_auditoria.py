"""Comprueba el registro de auditoría encadenado del libro de movimientos (motor común: `Ledger.verify`).

Cada cambio del libro se apunta también en `data/ledger.audit.jsonl` con la huella (hash) del anterior: si alguien
cambia, borra o reordena un apunte, o toca el libro a mano, aquí se ve. Los mensajes van en inglés (son del producto).

Uso:  python verificar_auditoria.py             la carpeta de datos de siempre; en el modo demo, si está vacía,
                                                la del último visitante de la pantalla
      python verificar_auditoria.py <carpeta>   otra: por ejemplo, la del zip que se baja en la pestaña «Audit trail»
                                                de la pantalla (13-nov: en la web, la carpeta de cada visitante solo
                                                existe en el servidor)
"""
import io
from pathlib import Path
import sys
import zipfile

from escenario import DATA  # añade motor_comun al path
from triage_core.ledger import Ledger


def carpeta_por_defecto() -> Path:
    """La de siempre. Si ahí no hay nada y la pantalla ya se usó en el modo demo, la del último visitante."""
    base = Path(DATA)
    if Ledger(base / "ledger.json").verify().events:
        return base
    visitantes = sorted((base / "visitantes").glob("*"), key=lambda c: c.stat().st_mtime)
    return visitantes[-1] if visitantes else base


def empaquetar(libro: Ledger) -> bytes:
    """El libro y su registro encadenado, en un zip, para comprobarlos en otro equipo con este mismo script. Es lo que
    baja la pestaña «Audit trail» de la pantalla."""
    zip_ = io.BytesIO()
    with zipfile.ZipFile(zip_, "w", zipfile.ZIP_DEFLATED) as z:
        for ruta in (libro.path, libro.audit_path):
            if ruta and Path(ruta).exists():
                z.write(Path(ruta), Path(ruta).name)
    return zip_.getvalue()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    carpeta = Path(argv[0]) if argv else carpeta_por_defecto()
    libro = Ledger(carpeta / "ledger.json")
    c = libro.verify()
    if not c.ok:
        print(f"PROBLEM in {libro.audit_path.name}: {c.problem}")
        return 1
    if not c.events:
        print(f"Nothing to check yet in {carpeta}: no audit events. Run a round first, or download the audit files "
              "from the demo's Audit trail tab and pass their folder")
        return 1
    print(f"Checked {carpeta}")
    print(f"OK: {c.events} events, every hash chains to the one before, and {c.entries_checked} ledger entries "
          f"match their trail" + (f" ({c.before_trail} older entries predate the trail)" if c.before_trail else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
