"""Vuelve a dejar el sandbox como al empezar la demo (38.000 $, 1.200 €, 0 £) y abre una ronda nueva.

1. Ronda nueva: lo pagado, convertido o aprobado antes no cuenta para la nueva demo (sigue en el libro)
2. Deposita lo que falte (depósito simulado) y manda el sobrante a la reserva del dueño (transferencia del sandbox)
Nunca imprime claves ni tokens.

Uso:  python reiniciar_demo.py
"""
import sys

import ajustar_saldos_demo
import preparar_sandbox
from escenario import nueva_ronda
from rail_airwallex import AirwallexError


def main() -> int:
    print(f"Ronda nueva: {nueva_ronda()}")
    preparar_sandbox.main()
    ajustar_saldos_demo.main()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AirwallexError as e:
        print(f"Error de Airwallex: {e}")
        sys.exit(1)
