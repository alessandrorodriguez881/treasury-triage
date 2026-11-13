"""The guide map (docs/GUIDE_MAP.md, 13-nov) links each point of the Airwallex builder guide to the exact line of the
code and of the test behind it. This keeps those links honest: each one must still point at what it names."""
from pathlib import Path
import re

RAIZ = Path(__file__).resolve().parents[1]
MAPA = RAIZ / "docs" / "GUIDE_MAP.md"
ENLACE = re.compile(r"\[`([A-Za-z_][A-Za-z0-9_]*)`\]\(\.\./([^)#]+)#L(\d+)\)")


def test_every_link_on_the_guide_map_points_at_what_it_names():
    enlaces = ENLACE.findall(MAPA.read_text(encoding="utf-8"))
    assert len(enlaces) >= 60
    for nombre, archivo, numero in enlaces:
        lineas = (RAIZ / archivo).read_text(encoding="utf-8").splitlines()
        assert int(numero) <= len(lineas), f"{archivo}#L{numero} is past the end of the file"
        linea = lineas[int(numero) - 1]
        assert re.search(rf"^\s*(?:(?:async\s+)?def|class)\s+{nombre}\b|^\s*{nombre}\s*[:=]", linea), \
            f"{archivo}#L{numero} should define {nombre}, but it says: {linea.strip()}"
