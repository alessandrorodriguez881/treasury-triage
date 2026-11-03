# Prueba con facturas reales: resultados (3-nov-2026)

Lo que se esperaba está en `HIPOTESIS.md`, escrito antes de ejecutar. El modelo es el de las dos apps (`triage_core/collections_model.py`), sin ningún cambio. Se repite con `python evaluar.py`, que escribe `resultados.json` y `calibracion.png`.

## En tres líneas
- **Con las 4.995 facturas de 2020 cuyo pago se conoce, se cumplen las cuatro hipótesis.** Entre la probabilidad que da el modelo y lo que pasó hay **2,6 puntos de media**, con **24 % menos error** que dar la misma probabilidad a todos los clientes.
- **Su «día 90 %»** (el pago llega como muy tarde ese día) **acertó en el 91,8 % de los pagos**; el modelo prometía el 93,2 %.
- **La prueba principal, tal como estaba escrita, falla la H3 (calibración).** La causa es un supuesto equivocado sobre los datos, no el modelo. Se explica abajo, con las dos cifras a la vista.

## Datos
- **«Payment Date Dataset»** de Kaggle (rajattomar132), licencia CC0: facturas de un proveedor de EE. UU. a grandes cadenas, de 2019 y 2020.
  - Copia: `govindk-18/B2B-Payment-Date-Prediction-Using-Regression`, `dataset.csv`, SHA-256 `14dfc3c0…5083535`. Sus primeras filas son las de la vista previa de Kaggle.
- **50.000 filas, 48.839 sin repetir.**
- **Las «abiertas» no son impagos.** Hay 9.681 facturas marcadas como abiertas y **todas son de facturas emitidas desde el 27-feb-2020**:
  - antes de esa fecha, ninguna sigue abierta;
  - después, solo 3 están cobradas;
  - de marzo a mayo, el 100 % está abierto.

  El archivo quitó la fecha de pago a todo lo emitido desde ese día; era el examen de un concurso de predicción. **En `HIPOTESIS.md` supuse que una abierta llevaba dos meses sin pagar, y era falso.**

## Resultados
**Facturas emitidas en enero y febrero de 2020.** Cada mes el modelo solo veía los pagos hechos antes del día 1. Brier: más bajo es mejor. ECE: distancia media entre lo predicho y lo real, en 10 grupos.

| | Prueba principal, tal como se escribió (348 «abiertas» contadas como tarde) | Solo facturas con el pago conocido |
|---|---|---|
| Facturas · clientes · pagadas a tiempo | 5.343 · 519 · 54,6 % | 4.995 · 497 · 58,4 % |
| **Nuestro modelo:** Brier · ECE · peor grupo | 0,2011 · 0,054 · 0,127 | **0,1857 · 0,026 · 0,071** |
| La misma tasa para todos (B0) | 0,2492 · 0,037 · 0,100 | 0,2429 · 0,023 · 0,055 |
| La media de cada cliente, sin encoger (B1) | 0,2024 · 0,064 · 0,135 | 0,1869 · 0,036 · 0,094 |
| El modelo con fuerza fija 4 (B2) | 0,2012 · 0,053 · 0,118 | 0,1859 · 0,019 · 0,041 |
| AUC del modelo (solo como referencia) | 0,747 | 0,775 |
| Intervalo del 5 al 95 %: real · prometido | 95,2 % · 96,4 % | 95,2 % · 96,4 % |
| Día 90 %: real · prometido | 91,8 % · 93,2 % | 91,8 % · 93,2 % |

| Hipótesis | Principal | Con el pago conocido |
|---|---|---|
| H1 · Mejor que la misma tasa para todos | Sí (−19 % de error) | **Sí (−24 %)** |
| H2 · No peor que la media de cada cliente | Sí | **Sí** |
| H2 · Mejor con clientes de menos de 5 facturas | Sí (0,214 frente a 0,232) | **Sí (0,211 frente a 0,233)** |
| H3 · Calibrado (ECE ≤ 0,05 y ningún grupo a más de 0,10) | **No** (0,054 y 0,127) | **Sí** (0,026 y 0,071) |
| H4 · Los intervalos cumplen lo que prometen (±5 puntos) | Sí | **Sí** |

**Error (Brier) según el historial del cliente**, con las facturas de pago conocido:

| Facturas anteriores del cliente | Facturas | Nuestro modelo | La media del cliente | La misma tasa para todos |
|---|---|---|---|---|
| 0 | 52 | 0,260 | 0,260 | 0,260 |
| 1-4 | 144 | **0,194** | 0,223 | 0,253 |
| 5-19 | 277 | **0,178** | 0,180 | 0,261 |
| 20 o más | 4.522 | **0,185** | 0,185 | 0,241 |

**Cómo leerlo:**
- **Conocer al cliente es lo que más ayuda:** −24 % de error.
- **Encoger hacia la media de la cartera ayuda sobre todo con pocos datos:** con 1-4 facturas anteriores, −13 % frente a la media del cliente sola. Es el caso de la demo.
- **Los intervalos de días se quedan un poco cortos** (1,2-1,4 puntos menos de lo prometido), dentro del margen.
- **El grupo que más se desvía:** en el de 25 %, pagaron a tiempo el 32 %.

## Lo que no demuestra (decirlo siempre)
- **Son grandes cadenas con plazos cortos** (casi todo a 15 días), no pequeños negocios como el de la demo, y es un solo proveedor.
- **Solo hay dos meses de prueba** (enero y febrero de 2020), porque después el archivo no trae fechas de pago.
- **No mide los recordatorios ni los descuentos:** estos datos no los tienen. Eso sigue saliendo solo de la simulación de 1.000 semanas.
- **Puede faltar algún impago antiguo:** antes del 27-feb no queda ninguna factura abierta, y quizá el archivo dejó fuera las que nunca se cobraron.
- **Con Terms Desk no se puede comparar.** Ellos predicen otra cosa (pagar dentro de la ventana de descuento): AUC 0,749 con el historial del cliente y 0,783 con su XGBoost. Nuestro 0,775 está en el mismo rango, pero no decimos que les ganemos.

## Qué cambia
- **En el modelo, nada.** La fuerza fija 4 sale un poco mejor calibrada aquí (ECE 0,019 frente a 0,026), con el mismo error.
  - El ajuste automático elige 1,7, porque estos clientes son muy distintos entre sí y tienen historiales largos.
  - No se cambia después de ver el resultado. Además, el 27-oct la fuerza 4 salió mal calibrada en la simulación.
- **En lo que contamos:**
  - **En el vídeo de PayPal:** la parte 7 dice «On five thousand real business invoices, its on-time odds were right to within three points, on average», sobre `paypal_2026/video/datos_reales.png`.
  - **En los README y los textos de entrega de las dos apps:** las cifras de «Solo facturas con el pago conocido», con el enlace a este archivo y la explicación de las «abiertas».
