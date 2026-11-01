"""Instala lo que necesita la comprobación del beneficiario en Metal, FUERA de la carpeta del proyecto.

Va a ~/.treasury-triage-metal (o a TT_METAL_RUNTIME): la CLI `metal-agent` y el servicio local «Payee Check»
(`verificador_x402.mjs`), a versiones que funcionan juntas. node_modules pesa unos 220 MB, así que no conviene
dentro de una carpeta sincronizada (OneDrive, Dropbox). Tampoco en %LOCALAPPDATA%: el Python de la Microsoft Store
desvía lo que escribe ahí. `npm ci` respeta el lock: @x402/* a 2.28.0 (con `npx` y @x402/fetch 2.29.0, `pay`
cobra pero acaba «en duda»).

Uso (desde app/):  python metal/preparar.py           instala (unos 2 minutos la primera vez; luego, nada)
                   python metal/preparar.py --clave   además crea la clave de pruebas del agente, si no existe,
                                                      en data/metal/agente.key.json y le pide *USDZ al grifo
Los mensajes van en inglés (son del producto). Nunca imprime la clave: solo su dirección.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

AQUI = Path(__file__).parent
APP = AQUI.parent
RUNTIME = Path(os.environ.get("TT_METAL_RUNTIME") or Path.home() / ".treasury-triage-metal")
FUENTES = ("package.json", "package-lock.json", "verificador_x402.mjs")
CLI_JS = RUNTIME / "node_modules" / "@metalntwx" / "metal-agent" / "dist" / "index.js"
CLAVE = APP / "data" / "metal" / "agente.key.json"


def copiar_fuentes() -> None:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    for nombre in FUENTES:
        if not (RUNTIME / nombre).exists() or (RUNTIME / nombre).read_bytes() != (AQUI / nombre).read_bytes():
            shutil.copy2(AQUI / nombre, RUNTIME / nombre)


def preparar() -> Path:
    copiar_fuentes()
    huella = hashlib.sha256((AQUI / "package-lock.json").read_bytes()).hexdigest()
    marca = RUNTIME / ".lock.sha256"
    if CLI_JS.exists() and marca.exists() and marca.read_text().strip() == huella:
        return RUNTIME
    npm = shutil.which("npm") or "npm"
    print(f"npm ci in {RUNTIME} (about 2 minutes the first time)...")
    subprocess.run([npm, "ci", "--no-audit", "--no-fund"], cwd=RUNTIME, check=True)
    marca.write_text(huella)
    return RUNTIME


def cli(*args: str) -> list[str]:
    hecho = subprocess.run([shutil.which("node") or "node", str(CLI_JS), *args], capture_output=True, text=True)
    lineas = hecho.stdout.splitlines() + [l for l in hecho.stderr.splitlines() if l.startswith("error:")]
    return lineas


def clave() -> None:
    if not CLAVE.exists():
        CLAVE.parent.mkdir(parents=True, exist_ok=True)
        for linea in cli("key", "create", "--out", str(CLAVE)):  # imprime la dirección y la ruta, nunca la clave
            print(linea)
    import json
    direccion = json.loads(CLAVE.read_text(encoding="utf-8"))["address"]
    print(f"agent address: {direccion}")
    for linea in cli("fund", direccion, "--network", "testnet"):
        print(linea)


if __name__ == "__main__":
    carpeta = preparar()
    print(f"ready: {carpeta} (metal-agent {' '.join(cli('--version'))})")
    if "--clave" in sys.argv[1:]:
        clave()
    sys.exit(0)
