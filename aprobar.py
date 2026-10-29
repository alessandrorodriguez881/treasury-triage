"""Tarjetas de aprobación de Treasury Triage: ver las pendientes y aprobar o rechazar una.

Uso:
  python aprobar.py                         lista las pendientes
  python aprobar.py a1b2c3d4                aprueba la tarjeta (el código sale en la tarjeta)
  python aprobar.py a1b2c3d4 --rechazar     la rechaza
  python aprobar.py a1b2c3d4 --quien CFO    deja apuntado quién aprueba (por defecto, owner)

Solo cambia el archivo de aprobaciones: el dinero se mueve después, con ejecutar_plan.py.
"""
import argparse
import sys

from aprobaciones import APROBACIONES, tarjeta
from triage_core.approvals import ApprovalBook


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("codigo", nargs="?")
    p.add_argument("--rechazar", action="store_true")
    p.add_argument("--quien", default="owner")
    args = p.parse_args()

    book = ApprovalBook.load(APROBACIONES)
    if not args.codigo:
        if not book.pending:
            print("No approvals pending")
        for fp, req in book.pending.items():
            print(tarjeta(fp, req))
        return 0

    encontradas = [fp for fp in book.pending if fp.startswith(args.codigo)]
    if len(encontradas) != 1:
        print(f"No single pending card with code {args.codigo}")
        return 1
    fp = encontradas[0]
    req = book.pending[fp]
    if args.rechazar:
        book.reject(fp, args.quien)
        print(f"❌ Rejected by {args.quien}: {req.counterparty} ({req.amount:,.2f} {req.currency})")
    else:
        book.approve(fp, args.quien)
        print(f"✅ Approved by {args.quien}: {req.counterparty} ({req.amount:,.2f} {req.currency})")
        print("   Money moves on the next run of ejecutar_plan.py")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace")  # una consola de Windows sin UTF-8 no se para por un emoji
    sys.exit(main())
