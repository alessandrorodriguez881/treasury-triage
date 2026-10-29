"""¿El sandbox de Airwallex de verdad o el simulador?

Sin claves de Airwallex en `.env` (o con TT_SIMULATED=1), todo corre contra `simulador_airwallex.py`, que responde
como el sandbox a las mismas llamadas REST: así el jurado prueba la demo sin crear cuentas. Cada modo guarda lo
suyo en su carpeta (`data/` el sandbox, `data_simulada/` el simulador), para no mezclar los libros de movimientos.
En el modo demo, cada visitante de la pantalla tiene además su propia carpeta (ver `CarpetaPorVisitante`).
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import time

from rail_airwallex import AirwallexRail, read_env

APP = Path(__file__).parent
ENV = APP / ".env"
SIMULADO = os.environ.get("TT_SIMULATED") == "1" or not read_env(ENV).get("AIRWALLEX_API_KEY")
_BASE = APP / ("data_simulada" if SIMULADO else "data")


def _visitante() -> str | None:
    """La sesión de Streamlit que está pintando la pantalla (cada pestaña del navegador es una), o None fuera de ella."""
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
    except ImportError:
        return None
    ctx = get_script_run_ctx(suppress_warning=True)
    return ctx.session_id if ctx else None


class CarpetaPorVisitante(os.PathLike):
    """Una ruta que se resuelve cada vez que se usa. En el modo demo, dentro de la pantalla, cada visitante tiene su
    carpeta (`visitantes/<sesión>/`): en una página pública, dos personas a la vez no se pisan, y recargar la página
    empieza una demo nueva. En la consola, en las pruebas o con el sandbox de verdad, es la carpeta de siempre."""

    def __init__(self, base: Path, partes: tuple[str, ...] = ()):
        self._base, self._partes = base, partes

    def resolver(self) -> Path:
        carpeta = self._base
        visitante = _visitante() if SIMULADO else None
        if visitante:
            carpeta = carpeta / "visitantes" / visitante
        carpeta.mkdir(parents=True, exist_ok=True)
        return carpeta.joinpath(*self._partes)

    def __truediv__(self, otra) -> "CarpetaPorVisitante":
        return CarpetaPorVisitante(self._base, self._partes + (str(otra),))

    def __fspath__(self) -> str:
        return str(self.resolver())

    __str__ = __fspath__

    def __repr__(self) -> str:
        return f"CarpetaPorVisitante({self.resolver()!r})"

    def __getattr__(self, nombre):  # exists, read_text, write_text, mkdir, parent, name, with_name...
        return getattr(self.resolver(), nombre)


def limpiar_visitantes(base: Path, horas: float = 24) -> None:
    """Borra las carpetas de los visitantes que llevan más de `horas` sin tocarse."""
    limite = time.time() - horas * 3600
    for carpeta in (base / "visitantes").glob("*") if (base / "visitantes").exists() else ():
        if carpeta.is_dir() and carpeta.stat().st_mtime < limite:
            shutil.rmtree(carpeta, ignore_errors=True)


DATA = CarpetaPorVisitante(_BASE)  # en el modo demo, una por visitante de la pantalla
if SIMULADO:
    limpiar_visitantes(_BASE)


def conectar() -> AirwallexRail:
    """La capa de Airwallex del modo actual. En el simulador, cada llamada relee el estado guardado en disco."""
    if SIMULADO:
        from simulador_airwallex import SandboxSimulado
        return AirwallexRail("simulated", "simulated", session=SandboxSimulado(DATA / "sandbox_simulado.json"),
                             sleep=lambda segundos: None)
    return AirwallexRail.from_env(ENV)
