# Prueba con facturas reales: lo que esperamos, escrito antes de mirar (3-nov-2026, 18:30)

**Qué se prueba:** el modelo de pagos que usan las dos apps (`triage_core/collections_model.py`: probabilidad de pagar a tiempo por cliente, con «encogimiento» hacia la media de la cartera, más los días de retraso cuando paga tarde). No se cambia nada del modelo para esta prueba.

## Datos
- **«Payment Date Dataset»** de Kaggle (rajattomar132), licencia **CC0 (dominio público)**: 50.000 facturas B2B reales de un vendedor de EE. UU. a grandes cadenas (Walmart, Target, Sysco…), 2019-2020. Es el mismo archivo que usa el rival Terms Desk.
- Copia usada: `dataset.csv` del repositorio público `govindk-18/B2B-Payment-Date-Prediction-Using-Regression` (7.072.048 bytes, SHA-256 `14dfc3c0c86d501457ba859271c63b51dbdf1e5c0af6e5ee2c8b96d6b5083535`). Sus primeras filas coinciden con la vista previa de Kaggle. No se sube al repositorio (`data/` queda fuera).
- **Limpieza:** se quitan las filas repetidas y las que repiten `doc_id` (se queda la primera). Cliente = `cust_number`. Vencimiento = `due_in_date`. Pago = `clear_date`.
- **«A tiempo»** = pagada el día del vencimiento o antes, como en el modelo (`days_late == 0`).

## Cómo se prueba (sin mirar el futuro)
- **Prueba principal:** las facturas **emitidas en enero y febrero de 2020** (`posting_date`). Al final de los datos (mayo de 2020) casi todas están cobradas. Una que siga abierta lleva más de dos meses vencida, así que cuenta como «tarde» (se dirá cuántas hay).
- **El modelo se reajusta al principio de cada mes** solo con los pagos hechos antes de ese día (`clear_date` anterior). Así trabajaría el agente: vuelve a leer el historial de cobros cada cierto tiempo.
- **Secundaria (para comparar con Terms Desk):** todas las facturas de 2020 ya cobradas. Sesgo conocido: las más recientes que siguen abiertas son justo las que pagan tarde.

## Con qué se compara
- **B0, «todos igual»:** la misma probabilidad para todos los clientes (la media de la cartera).
- **B1, «media del cliente sin encoger»:** la proporción a tiempo de cada cliente (la de la cartera si no tiene historial).
- **B2, el modelo con la fuerza de encogimiento fija en 4**, como estaba antes del 27-oct.

## Medidas
- **Puntuación de Brier** de la probabilidad de pagar a tiempo (más baja es mejor).
- **Calibración:** 10 grupos del mismo tamaño ordenados por la probabilidad dada. El error medio entre lo predicho y lo ocurrido (ECE) y el peor grupo.
- **Cobertura del intervalo del 90 % de días de retraso** (del 5 % al 95 % de la distribución del modelo), en las facturas ya cobradas.
- Solo como referencia: AUC y pérdida logarítmica. Terms Desk mide otra cosa (pagar dentro de la ventana de descuento), así que sus cifras **no son comparables** con las nuestras.
- También por tamaño del historial del cliente: 0, 1-4, 5-19 y 20 o más facturas anteriores.

## Hipótesis
- **H1.** Nuestro modelo tiene un Brier más bajo que B0 («todos igual»): conocer al cliente ayuda.
- **H2.** Nuestro modelo no es peor que B1 en el total, y es mejor que B1 en los clientes con menos de 5 facturas anteriores.
- **H3.** Está bien calibrado: ECE ≤ 0,05 y ningún grupo se desvía más de 0,10.
- **H4.** El intervalo del 90 % de días de retraso acierta lo que promete: la parte de pagos reales que cae dentro se aleja como mucho 5 puntos de la que el propio modelo da a ese intervalo.
  - *Corregido a las 18:40, antes de ejecutar nada:* pagar a tiempo es un pico en «0 días», así que cuando el 5 % cae en ese pico el intervalo cubre más del 90 % por construcción (el 95 % o más).
  - Por eso se compara con la cobertura que da el propio modelo, factura a factura, y no con un 90 % fijo. Lo mismo para la cota de arriba sola (el día 90 %: «el pago llega como muy tarde este día»).

## Qué haremos con el resultado
- Se cuenta **lo que salga**, también lo que falle, en `RESULTADOS.md`, y en el README y en Devpost solo lo que se cumpla.
- **No se toca el modelo antes de contar el resultado.** Si algo falla y se arregla, el arreglo se prueba aparte, con las facturas de marzo a mayo de 2020, que la prueba principal no usa, y se dice así.
- Límite que se dirá siempre: son grandes cadenas con plazos cortos (casi todo a 15 días), no pequeños negocios como los de la demo.
