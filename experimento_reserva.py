"""Experimento con la semilla YA USADA 2027: ¿y si el pago que para la empresa se paga con la reserva?

Hoy el agente nunca planea bajar de la reserva: si para no tocarla tiene que aplazar el pago que para la empresa
(el transporte), lo aplaza, y eso es unas dos terceras partes de su coste. Con `reserve_covers_must_pay`, en esas
semanas puede planear pagar SOLO ese pago con la reserva (nunca por debajo de cero) y una persona lo aprueba.
Aquí se supone que la persona aprueba. No cuenta como prueba: es para decidir si se cambia la política.

Uso:  python experimento_reserva.py [--semilla 2027]
"""
from __future__ import annotations

import argparse

from simulacion import resumir, simular
from triage_core.policy import TreasuryPolicy


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--semilla", type=int, default=2027)
    args = ap.parse_args()
    for nombre, pol in (("como ahora", TreasuryPolicy()),
                        ("reserva para el pago imprescindible", TreasuryPolicy(reserve_covers_must_pay=True))):
        r = resumir(simular(1000, args.semilla, pol))
        x = r["agentes"]["Treasury Triage"]
        print(f"{nombre:<38} reserva rota {x['reserva_rota_pct']:4.1f}% | empresa parada {x['empresa_parada_pct']:4.1f}%"
              f" | coste {x['coste_medio']:5.0f} $ | pagado {x['pagado_medio_usd']:6.0f} $"
              f" | pagar por orden: rota {r['agentes']['Pagar por orden']['reserva_rota_pct']:.1f}%")


if __name__ == "__main__":
    main()
