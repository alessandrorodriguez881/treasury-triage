"""Prueba de conexión con el sandbox de Airwallex.

Lee las claves de .env, hace login, lista las cuentas globales y los saldos.
Nunca imprime claves ni tokens. Solo lee: no crea ni mueve nada.

Uso:  python probar_airwallex.py
"""
from pathlib import Path
import sys

import requests

BASE = "https://api.sandbox.airwallex.com"


def leer_env(ruta: Path) -> dict:
    valores = {}
    for linea in ruta.read_text(encoding="utf-8-sig").splitlines():
        linea = linea.strip()
        if linea and not linea.startswith("#") and "=" in linea:
            clave, valor = linea.split("=", 1)
            valores[clave.strip()] = valor.strip()
    return valores


def main() -> int:
    env = leer_env(Path(__file__).with_name(".env"))
    if env.get("AIRWALLEX_ENVIRONMENT", "SANDBOX").upper() != "SANDBOX":
        print("Parado: esta prueba solo se ejecuta contra el SANDBOX.")
        return 1
    cliente, clave = env.get("AIRWALLEX_CLIENT_ID", ""), env.get("AIRWALLEX_API_KEY", "")
    if not cliente or not clave:
        print("Faltan AIRWALLEX_CLIENT_ID o AIRWALLEX_API_KEY en .env")
        return 1

    r = requests.post(f"{BASE}/api/v1/authentication/login", timeout=30,
                      headers={"x-client-id": cliente, "x-api-key": clave})
    print(f"1. Login: HTTP {r.status_code}")
    if not r.ok:
        error = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        print(f"   Error de Airwallex: {error.get('code')} - {error.get('message')}")
        print("   Las claves no valen, o la clave no tiene permisos suficientes")
        return 1
    cab = {"Authorization": f"Bearer {r.json()['token']}"}

    r = requests.get(f"{BASE}/api/v1/global_accounts", headers=cab, timeout=30)
    cuentas = r.json().get("items", []) if r.ok else []
    print(f"2. Cuentas globales: HTTP {r.status_code} ({len(cuentas)} cuentas)")
    for c in cuentas[:5]:
        print(f"   {c.get('country_code')} {c.get('status')} divisas={[f.get('currency') for f in c.get('supported_features', [])][:3]}")

    r = requests.get(f"{BASE}/api/v1/balances/current", headers=cab, timeout=30)
    print(f"3. Saldos: HTTP {r.status_code}")
    if r.ok:
        for s in r.json()[:8]:
            print(f"   {s.get('currency')}: disponible {s.get('available_amount')}")
    return 0 if r.ok else 1


if __name__ == "__main__":
    sys.exit(main())
