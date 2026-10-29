"""Gráficas de la simulación de 1.000 semanas para el vídeo y el README (en inglés, para el jurado).

Forma «emphasis» (guía de dataviz): Treasury Triage en el color de acento y los demás agentes en gris.
Barras finas con el extremo redondeado, cuadradas en la base; rejilla fina; valores al final de cada barra.

Uso:  python graficas.py [resultados/simulacion_1000_normales_semilla2031.json]
"""
from pathlib import Path
import json
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle
from matplotlib.ticker import MaxNLocator

SURFACE, INK, INK_2, MUTED, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
ACCENT, DEEMPH = "#2a78d6", "#c3c2b7"
NOMBRES = {"Treasury Triage": "Treasury Triage", "Treasury Triage sin tocar la reserva": "Same engine, reserve untouched",
           "Treasury Triage sin aprender": "Same engine, never learns",
           "Treasury Triage sin correos": "Same engine, emails ignored",
           "Receta sin previsión": "Recipe, no forecast", "Pagar por orden": "Pay in due-date order"}
DPI = 200

plt.rcParams.update({"font.family": ["Segoe UI", "DejaVu Sans"], "font.size": 10, "text.color": INK,
                     "axes.edgecolor": AXIS, "axes.labelcolor": INK_2, "xtick.color": MUTED, "ytick.color": INK_2})


def _barra(ax, y: float, valor: float, alto: float, color: str, r_px: float = 4) -> None:
    """Barra horizontal con el extremo de datos redondeado (r_px) y la base cuadrada."""
    if valor <= 0:
        return
    fig = ax.figure
    bbox = ax.get_window_extent()
    x_por_px = (ax.get_xlim()[1] - ax.get_xlim()[0]) / bbox.width
    y_por_px = (ax.get_ylim()[1] - ax.get_ylim()[0]) / bbox.height
    r = min(r_px * x_por_px, valor / 2)
    ax.add_patch(FancyBboxPatch((0, y - alto / 2), valor, alto, boxstyle=f"round,pad=0,rounding_size={r}",
                                mutation_aspect=y_por_px / x_por_px, facecolor=color, edgecolor="none"))
    ax.add_patch(Rectangle((0, y - alto / 2), min(r * 1.5, valor), alto, facecolor=color, edgecolor="none"))


def agentes(res: dict, salida: Path) -> None:
    filas = [a for a in NOMBRES if a in res["agentes"]][::-1]  # Triage arriba; las dos primeras tandas no tienen
    # el agente sin aprender
    reserva = "Treasury Triage sin tocar la reserva" in res["agentes"]  # cuarta tanda: la reserva paga lo imprescindible
    paneles = [("Weeks below the cash reserve" if reserva else "Weeks the cash reserve was broken", "reserva_rota_pct",
                "{:.1f}%"),
               ("Weeks the business stopped (must-pay bill deferred)" if reserva else "Weeks a must-pay bill was deferred",
                "empresa_parada_pct", "{:.1f}%"),
               ("Average cost per week (fees, FX, late costs)", "coste_medio", "${:,.0f}")]
    fig, axes = plt.subplots(1, 3, figsize=(13, 3.0 + 0.3 * len(filas)), dpi=DPI, facecolor=SURFACE)
    fig.subplots_adjust(left=0.17, right=0.98, top=0.74, bottom=0.12, wspace=0.12)
    for k, (ax, (titulo, campo, fmt)) in enumerate(zip(axes, paneles)):
        valores = [res["agentes"][a][campo] for a in filas]
        tope = max(valores) * 1.28 or 1
        ax.set_facecolor(SURFACE)
        ax.set_xlim(0, tope)
        ax.set_ylim(-0.6, len(filas) - 0.4)
        fig.canvas.draw()
        alto = 18 * (ax.get_ylim()[1] - ax.get_ylim()[0]) / ax.get_window_extent().height  # 18 px, bajo el tope de 24
        for i, (a, v) in enumerate(zip(filas, valores)):
            _barra(ax, i, v, alto, ACCENT if a == "Treasury Triage" else DEEMPH)
            ax.text(v + tope * 0.015, i, fmt.format(v), va="center", ha="left", fontsize=9.5,
                    color=INK if a == "Treasury Triage" else INK_2,
                    fontweight="semibold" if a == "Treasury Triage" else "normal")
        ax.set_title(titulo, loc="left", fontsize=10.5, color=INK, pad=8)
        ax.set_yticks(range(len(filas)))
        ax.set_yticklabels([NOMBRES[a] for a in filas] if k == 0 else [], fontsize=10)
        ax.tick_params(axis="y", length=0)
        ax.tick_params(axis="x", length=0, labelsize=8.5)
        ax.xaxis.grid(True, color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        for lado in ("top", "right", "bottom"):
            ax.spines[lado].set_visible(False)
        ax.spines["left"].set_color(AXIS)
        ax.xaxis.set_major_locator(MaxNLocator(nbins=4, steps=[1, 2, 5, 10]))  # marcas redondas: 0, 5, 10, 15
        ax.xaxis.set_major_formatter(plt.FuncFormatter(
            (lambda x, _: f"{x:g}%") if "%" in fmt else (lambda x, _: f"${x:,.0f}")))
    titulo = ("never stops the business and costs far less than the no-forecast recipe" if reserva
              else "keeps the reserve and costs less than the no-forecast recipe")
    fig.text(0.012, 0.95, f"{res['semanas']:,} simulated weeks: Treasury Triage {titulo}", fontsize=13,
             fontweight="semibold", color=INK)
    fig.text(0.012, 0.89, "Same synthetic weeks for every agent. Customers' true on-time odds are hidden from all of "
             "them. Reserve floor $15,000; policy exceptions go to a person in every case."
             + ("\nTreasury Triage uses the reserve only for the bill that stops the business, never below zero cash, "
                "and only with a person's approval." if reserva else ""), fontsize=9.5, color=INK_2, va="top")
    fig.savefig(salida, facecolor=SURFACE)
    plt.close(fig)


def calibracion(res: dict, salida: Path) -> None:
    tramos = [t for t in res["calibracion"]["tramos"] if t["n"] >= 50]
    fig, ax = plt.subplots(figsize=(6.2, 5.4), dpi=DPI, facecolor=SURFACE)
    fig.subplots_adjust(left=0.14, right=0.96, top=0.80, bottom=0.12)
    ax.set_facecolor(SURFACE)
    ax.plot([0, 1], [0, 1], color=AXIS, linewidth=1, zorder=1)
    c = res["calibracion"]
    antes = [t for t in c.get("tramos_sin_aprender", []) if t["n"] >= 50]
    if antes:  # tercera tanda: el mismo motor sin aprender, en gris
        xa, ya = [t["previsto"] for t in antes], [t["observado"] for t in antes]
        ax.plot(xa, ya, color=DEEMPH, linewidth=1.6, solid_capstyle="round", solid_joinstyle="round", zorder=2)
        ax.scatter(xa, ya, s=40, color=DEEMPH, edgecolors=SURFACE, linewidths=1.5, zorder=2)
        k = len(xa) // 2
        ax.text(xa[k] - 0.015, ya[k] + 0.02, "never learns", ha="right", va="bottom", fontsize=8.5, color=MUTED)
    x = [t["previsto"] for t in tramos]
    y = [t["observado"] for t in tramos]
    ax.plot(x, y, color=ACCENT, linewidth=2, solid_capstyle="round", solid_joinstyle="round", zorder=3)
    ax.scatter(x, y, s=70, color=ACCENT, edgecolors=SURFACE, linewidths=2, zorder=4)
    if antes:
        k = len(x) // 2
        ax.text(x[k] + 0.01, y[k] - 0.07, "learns from every payment", ha="left", va="top", fontsize=8.5,
                color=ACCENT, fontweight="semibold")
    peor = max(tramos, key=lambda t: abs(t["observado"] - t["previsto"]))
    ax.annotate(f"worst gap {abs(peor['observado'] - peor['previsto']) * 100:.0f} pts\n"
                f"(predicted {peor['previsto']:.0%}, paid on time {peor['observado']:.0%})",
                (peor["previsto"], peor["observado"]), xytext=(28, -6), textcoords="offset points", va="center",
                fontsize=8.5, color=INK_2, arrowprops={"arrowstyle": "-", "color": MUTED, "linewidth": 0.8})
    abajo = min([0.3] + [int(min(t["previsto"], t["observado"]) * 10) / 10 for t in tramos + antes])
    ax.set_xlim(abajo, 1.0)
    ax.set_ylim(abajo, 1.0)
    if antes:  # abajo a la derecha de la diagonal queda libre: arriba la pisaría la línea gris
        ax.text(abajo + 0.17, abajo + 0.12, "perfect calibration", ha="left", va="center", fontsize=8.5, color=MUTED)
    else:
        ax.text(0.97, 0.93, "perfect calibration", ha="right", va="bottom", fontsize=8.5, color=MUTED)
    ax.set_xlabel("Predicted probability of paying on time", fontsize=9.5)
    ax.set_ylabel("Share that really paid on time", fontsize=9.5)
    for eje in (ax.xaxis, ax.yaxis):
        eje.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.0%}"))
    ax.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.tick_params(length=0, labelsize=8.5)
    for lado in ("top", "right"):
        ax.spines[lado].set_visible(False)
    fig.text(0.02, 0.93, "Does the forecast know how much to trust itself?", fontsize=12.5, fontweight="semibold")
    if antes:
        detalle = (f"{c['brier_con_correos']:.3f} learning · {c['brier_sin_aprender']:.3f} never learning · "
                   f"{c['brier_verdad']:.3f} if it knew the hidden truth")
    else:
        detalle = (f"{c['brier_con_correos']:.3f} with emails · {c['brier_sin_correos']:.3f} history only · "
                   f"{c['brier_media_fija']:.3f} one fixed rate for everyone")
    fig.text(0.02, 0.855, f"Each dot: receipts grouped by predicted odds (groups of 50+). Error score (Brier, lower "
             f"is better):\n{detalle}", fontsize=8.8, color=INK_2)
    fig.savefig(salida, facecolor=SURFACE)
    plt.close(fig)


def main() -> int:
    ruta = Path(sys.argv[1]) if len(sys.argv) > 1 else \
        Path(__file__).with_name("resultados") / "simulacion_1000_normales_semilla2031.json"
    res = json.loads(ruta.read_text(encoding="utf-8"))
    agentes(res, ruta.with_name(ruta.stem + "_agentes.png"))
    calibracion(res, ruta.with_name(ruta.stem + "_calibracion.png"))
    print("Gráficas guardadas junto a", ruta.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
