"""Prueba del modelo de pagos con facturas reales: Kaggle «Payment Date Dataset» (CC0), 50.000 facturas B2B.

Lo que se espera está escrito antes en HIPOTESIS.md. El modelo no se toca: se usa tal cual lo usan las dos apps.
Cada mes se reajusta solo con los pagos hechos antes de ese día, y predice las facturas emitidas ese mes.

Uso:  python evaluar.py      (lee data/dataset.csv; escribe resultados.json y calibracion.png)
"""
from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))
from triage_core.collections_model import CollectionsModel, PaidInvoice  # noqa: E402

DATOS = AQUI / "data" / "dataset.csv"
SHA256 = "14dfc3c0c86d501457ba859271c63b51dbdf1e5c0af6e5ee2c8b96d6b5083535"
FUENTE = "https://www.kaggle.com/datasets/rajattomar132/payment-date-dataset"
COPIA = ("https://raw.githubusercontent.com/govindk-18/B2B-Payment-Date-Prediction-Using-Regression/"
         "main/dataset.csv")
PRINCIPAL = [(2020, 1), (2020, 2)]
SECUNDARIA = [(2020, m) for m in range(1, 6)]
TRAMOS = [(0, 0, "0"), (1, 4, "1-4"), (5, 19, "5-19"), (20, 10**9, "20+")]


def cargar() -> pd.DataFrame:
    """Facturas sin repetir: cliente, emisión, vencimiento y pago (None si sigue abierta)."""
    if hashlib.sha256(DATOS.read_bytes()).hexdigest() != SHA256:
        raise SystemExit(f"{DATOS.name} no es el archivo esperado: bájalo de {COPIA}")
    df = pd.read_csv(DATOS, dtype={"cust_number": str})
    filas = len(df)
    df = df.drop_duplicates().drop_duplicates(subset="doc_id", keep="first")
    df = df[df["due_in_date"].notna()].copy()
    df["cliente"] = df["cust_number"].str.strip()
    df["vence"] = pd.to_datetime(df["due_in_date"].astype("int64").astype(str), format="%Y%m%d").dt.date
    df["emitida"] = pd.to_datetime(df["posting_date"], format="%d-%m-%Y").dt.date
    pagos = pd.to_datetime(df["clear_date"], format="%d-%m-%Y %H:%M")
    df["pagada"] = [p.date() if pd.notna(p) else None for p in pagos]
    df.attrs["filas"], df.attrs["sin_repetir"] = filas, len(df)
    # Las «abiertas» no son impagos: desde un día dado, el archivo quita la fecha de pago a todas (era el examen
    # de un concurso). Se comprueba aquí, para no contarlas como pagos tardíos.
    abiertas = df["isOpen"] == 1
    corte = min(df.loc[abiertas, "emitida"])
    despues = df["emitida"] >= corte
    df.attrs["abiertas"] = {"desde": corte.isoformat(), "abiertas": int(abiertas.sum()),
                            "abiertas_antes": int((abiertas & ~despues).sum()),
                            "cobradas_despues": int((~abiertas & despues).sum())}
    return df


def historial(df: pd.DataFrame, corte: date) -> list[PaidInvoice]:
    """Lo que el agente sabría ese día: solo las facturas ya cobradas antes del corte."""
    vistas = df[[p is not None and p < corte for p in df["pagada"]]]
    return [PaidInvoice(c, v, p, float(a)) for c, v, p, a in
            zip(vistas["cliente"], vistas["vence"], vistas["pagada"], vistas["total_open_amount"])]


def cuantil(p0: float, tarde: np.ndarray, nivel: float) -> int:
    """Día de retraso en el que la distribución del modelo llega a `nivel` (0 = a tiempo)."""
    if nivel <= p0:
        return 0
    return int(np.quantile(tarde, (nivel - p0) / (1 - p0), method="inverted_cdf"))


def acumulada(p0: float, tarde: np.ndarray, dia: int) -> float:
    """Probabilidad que da el modelo a pagar con `dia` días de retraso o menos."""
    return 0.0 if dia < 0 else p0 + (1 - p0) * float(np.mean(tarde <= dia))


def predecir(df: pd.DataFrame, meses: list[tuple[int, int]]) -> pd.DataFrame:
    """Una fila por factura de prueba con lo que dice cada modelo y lo que pasó de verdad."""
    salida = []
    for y, m in meses:
        corte = date(y, m, 1)
        hist = historial(df, corte)
        modelo, fijo = CollectionsModel(hist), CollectionsModel(hist, prior_strength=4)
        cuenta = pd.Series([h.days_late == 0 for h in hist]).groupby([h.customer for h in hist]).agg(["sum", "count"])
        prueba = df[[e.year == y and e.month == m for e in df["emitida"]]]
        perfiles, perfiles_fijo = {}, {}
        for fila in prueba.itertuples():
            c = fila.cliente
            if c not in perfiles:
                perfiles[c], perfiles_fijo[c] = modelo.profile(c), fijo.profile(c)
            pf, pf4 = perfiles[c], perfiles_fijo[c]
            k, n = (cuenta.loc[c, "sum"], cuenta.loc[c, "count"]) if c in cuenta.index else (0, 0)
            pagada = fila.pagada
            dias = None if pagada is None else max(0, (pagada - fila.vence).days)
            q05, q90, q95 = (cuantil(pf.p_on_time, pf.late_days, a) for a in (0.05, 0.90, 0.95))
            salida.append({
                "mes": f"{y}-{m:02d}", "cliente": c, "previas": pf.n_invoices,
                "a_tiempo": int(dias == 0), "cobrada": pagada is not None, "dias": dias,
                "modelo": pf.p_on_time, "fijo_4": pf4.p_on_time, "todos_igual": modelo.pool_rate,
                "media_cliente": k / n if n else modelo.pool_rate,
                "q05": q05, "q90": q90, "q95": q95,
                "masa_90_95": acumulada(pf.p_on_time, pf.late_days, q95)
                              - acumulada(pf.p_on_time, pf.late_days, q05 - 1),
                "masa_90": acumulada(pf.p_on_time, pf.late_days, q90),
                "fuerza": modelo.prior_strength,
            })
    return pd.DataFrame(salida)


def brier(p, y) -> float:
    return float(np.mean((p - y) ** 2))


def perdida_log(p, y) -> float:
    p = np.clip(p, 0.01, 0.99)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def auc(p, y) -> float:
    r = pd.Series(p).rank().to_numpy()
    n1, n0 = int(y.sum()), int(len(y) - y.sum())
    return float((r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def calibracion(p, y, grupos: int = 10) -> dict:
    trozos = [t for t in np.array_split(np.argsort(p, kind="stable"), grupos) if len(t)]
    filas = [{"predicho": float(p[t].mean()), "real": float(y[t].mean()), "n": len(t)} for t in trozos]
    return {"grupos": filas,
            "ece": sum(f["n"] * abs(f["predicho"] - f["real"]) for f in filas) / len(p),
            "peor_grupo": max(abs(f["predicho"] - f["real"]) for f in filas)}


MODELOS = {"modelo": "Our model (shrunk, as in the apps)", "fijo_4": "Same, fixed strength 4 (before 27-oct)",
           "media_cliente": "Customer's own rate, not shrunk", "todos_igual": "Everyone the same (portfolio rate)"}


def medir(t: pd.DataFrame) -> dict:
    y = t["a_tiempo"].to_numpy(dtype=float)
    out = {"facturas": len(t), "clientes": int(t["cliente"].nunique()), "a_tiempo_real": float(y.mean()),
           "abiertas_contadas_tarde": int((~t["cobrada"]).sum()),
           "fuerza_encogimiento": sorted({round(float(f), 1) for f in t["fuerza"]})}
    for clave in MODELOS:
        p = t[clave].to_numpy()
        cal = calibracion(p, y)
        out[clave] = {"brier": brier(p, y), "log": perdida_log(p, y), "auc": auc(p, y),
                      "ece": cal["ece"], "peor_grupo": cal["peor_grupo"], "grupos": cal["grupos"]}
    out["por_historial"] = {}
    for bajo, alto, nombre in TRAMOS:
        s = t[(t["previas"] >= bajo) & (t["previas"] <= alto)]
        if len(s):
            ys = s["a_tiempo"].to_numpy(dtype=float)
            out["por_historial"][nombre] = {"facturas": len(s), **{c: brier(s[c].to_numpy(), ys) for c in MODELOS}}
    c = t[t["cobrada"]]
    dentro = (c["dias"] >= c["q05"]) & (c["dias"] <= c["q95"])
    out["intervalo"] = {"facturas_cobradas": len(c),
                        "intervalo_5_95": {"real": float(dentro.mean()), "promete": float(c["masa_90_95"].mean())},
                        "cota_90": {"real": float((c["dias"] <= c["q90"]).mean()), "promete": float(c["masa_90"].mean())}}
    return out


def hipotesis(r: dict) -> dict:
    m, b0, b1 = r["modelo"], r["todos_igual"], r["media_cliente"]
    poco = r["por_historial"]
    pocas = [poco[k] for k in ("0", "1-4") if k in poco]
    n = sum(x["facturas"] for x in pocas)
    pocas_modelo = sum(x["facturas"] * x["modelo"] for x in pocas) / n if n else None
    pocas_b1 = sum(x["facturas"] * x["media_cliente"] for x in pocas) / n if n else None
    i = r["intervalo"]
    return {
        "H1 better than everyone-the-same": m["brier"] < b0["brier"],
        "H2 not worse than customer's own rate": m["brier"] <= b1["brier"],
        "H2 better on customers with <5 invoices": pocas_modelo is not None and pocas_modelo < pocas_b1,
        "H3 calibrated (ECE <= 0.05, worst group <= 0.10)": m["ece"] <= 0.05 and m["peor_grupo"] <= 0.10,
        "H4 5-95 interval keeps its promise (+-5 pts)":
            abs(i["intervalo_5_95"]["real"] - i["intervalo_5_95"]["promete"]) <= 0.05,
        "H4 90% bound keeps its promise (+-5 pts)": abs(i["cota_90"]["real"] - i["cota_90"]["promete"]) <= 0.05,
    }


def grafica(t: pd.DataFrame, ruta: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    y = t["a_tiempo"].to_numpy(dtype=float)
    fig, ax = plt.subplots(figsize=(6.4, 6.0), dpi=150)
    ax.plot([0, 1], [0, 1], color="#9ca3af", lw=1, ls="--", label="Perfect calibration")
    for clave, color, marca in (("todos_igual", "#9ca3af", "s"), ("modelo", "#0f766e", "o")):
        g = calibracion(t[clave].to_numpy(), y)["grupos"]
        ax.plot([f["predicho"] for f in g], [f["real"] for f in g], marker=marca, color=color, lw=1.5,
                label=MODELOS[clave])
    ax.set_xlim(0, 1), ax.set_ylim(0, 1)
    ax.set_xlabel("Predicted chance of paying on time"), ax.set_ylabel("Share that really paid on time")
    ax.set_title(f"{len(t):,} real B2B invoices from 2020 (each model only saw earlier payments)", fontsize=9)
    ax.legend(loc="upper left", fontsize=8, frameon=False)
    for lado in ("top", "right"):
        ax.spines[lado].set_visible(False)
    fig.tight_layout()
    fig.savefig(ruta)
    plt.close(fig)


def main() -> int:
    df = cargar()
    principal, secundaria = predecir(df, PRINCIPAL), predecir(df, SECUNDARIA)
    secundaria = secundaria[secundaria["cobrada"]]
    r = {"fuente": FUENTE, "copia": COPIA, "sha256": SHA256, "filas": df.attrs["filas"],
         "sin_repetir": df.attrs["sin_repetir"], "abiertas": df.attrs["abiertas"],
         "principal": medir(principal), "secundaria": medir(secundaria)}
    r["hipotesis"] = hipotesis(r["principal"])
    r["hipotesis_secundaria"] = hipotesis(r["secundaria"])
    (AQUI / "resultados.json").write_text(json.dumps(r, indent=1, ensure_ascii=False), encoding="utf-8")
    grafica(secundaria, AQUI / "calibracion.png")
    a = r["abiertas"]
    print(f"Invoices: {r['filas']:,} rows, {r['sin_repetir']:,} without repeats. Marked open: {a['abiertas']:,}, "
          f"all issued from {a['desde']} (open before: {a['abiertas_antes']}, paid after: {a['cobradas_despues']})")
    for titulo, clave_r, clave_h in (("Main test as written (open ones counted late)", "principal", "hipotesis"),
                                     ("Invoices with a known payment date", "secundaria", "hipotesis_secundaria")):
        p = r[clave_r]
        print(f"{titulo}: {p['facturas']:,} invoices from {p['clientes']} customers; "
              f"{p['a_tiempo_real']:.1%} on time; open counted late: {p['abiertas_contadas_tarde']}")
        for clave, nombre in MODELOS.items():
            x = p[clave]
            print(f"  {nombre:42s} Brier {x['brier']:.4f}  ECE {x['ece']:.3f}  worst {x['peor_grupo']:.3f}  "
                  f"AUC {x['auc']:.3f}")
        i = p["intervalo"]
        print(f"  5-95 interval: real {i['intervalo_5_95']['real']:.1%} vs promised "
              f"{i['intervalo_5_95']['promete']:.1%} | 90% bound: real {i['cota_90']['real']:.1%} vs promised "
              f"{i['cota_90']['promete']:.1%}")
        for k, v in r[clave_h].items():
            print(f"  {'OK  ' if v else 'FAIL'} {k}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
