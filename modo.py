"""¿El sandbox de Airwallex de verdad o el simulador?

Sin claves de Airwallex en `.env` (o con TT_SIMULATED=1), todo corre contra `simulador_airwallex.py`, que responde
como el sandbox a las mismas llamadas REST: así el jurado prueba la demo sin crear cuentas. Cada modo guarda lo
suyo en su carpeta (`data/` el sandbox, `data_simulada/` el simulador), para no mezclar los libros de movimientos.
"""
from __future__ import annotations

import os
from pathlib import Path

from rail_airwallex import AirwallexRail, read_env

APP = Path(__file__).parent
ENV = APP / ".env"
SIMULADO = os.environ.get("TT_SIMULATED") == "1" or not read_env(ENV).get("AIRWALLEX_API_KEY")
DATA = APP / ("data_simulada" if SIMULADO else "data")


def conectar() -> AirwallexRail:
    """La capa de Airwallex del modo actual. En el simulador, cada llamada relee el estado guardado en disco."""
    if SIMULADO:
        from simulador_airwallex import SandboxSimulado
        return AirwallexRail("simulated", "simulated", session=SandboxSimulado(DATA / "sandbox_simulado.json"),
                             sleep=lambda segundos: None)
    return AirwallexRail.from_env(ENV)
