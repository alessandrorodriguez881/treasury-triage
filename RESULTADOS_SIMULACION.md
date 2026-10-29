# Simulación de 1.000 semanas (27-oct-2026)

**Pregunta:** ¿decide mejor un agente que sabe cuánto fiarse de su previsión de cobros?
Código: `app/simulacion.py` (hipótesis escritas en el código antes de ejecutar), gráficas en `app/graficas.py`, resultados en `app/resultados/`. 7 pruebas en `app/tests/test_simulacion.py`.

## Cómo se ha hecho
- **1.000 semanas sintéticas.** Cada una tiene saldos en USD, EUR y GBP, entre 3 y 5 pagos que vencen en 72 h (siempre uno que, si no se paga, para la empresa) y 2-3 cobros esperados
- **30 clientes con una probabilidad verdadera de pagar a tiempo que ningún agente ve.** El motor solo ve su historial (de 3 a 24 facturas)
- **Correos de clientes:** «pagamos la semana que viene» aparece en el 40 % de los cobros que llegarán tarde y en el 14 % de los que llegarán a tiempo
- **Los cuatro agentes deciden con la misma información y se mide lo que pasa de verdad:**
  1. **Treasury Triage:** nuestro motor
  2. **El mismo motor sin leer los correos**
  3. **La receta sin previsión:** primero lo urgente y lo caro de retrasar, y solo paga si el saldo de hoy no baja de la reserva
  4. **Pagar por orden:** por fecha, mientras haya dinero
- Las cuentas (recargos, cambio, comisiones) son las del motor, iguales para todos. Las excepciones de política (proveedor nuevo) van siempre a una persona

## Resultados

### Simulación fijada de antemano (semilla 2026, clientes «normales», que pagan a tiempo el 75 % de las veces)
| Agente | Semanas con la reserva rota | Semanas con un pago imprescindible aplazado | Coste por semana | Pagado por semana |
|---|---|---|---|---|
| **Treasury Triage** | **0,5 %** | 13,2 % | **1.010 $** | 18.448 $ |
| Sin leer correos | 0,9 % | 12,6 % | 956 $ | 18.914 $ |
| Receta sin previsión | 0,0 % | 16,8 % | 1.355 $ | 15.541 $ |
| Pagar por orden | 13,8 % | 0,0 % | 116 $ | 22.670 $ |

| Hipótesis | Resultado |
|---|---|
| H1. Rompe la reserva menos que «pagar por orden» | ✅ −13,3 puntos (IC 95 %: −15,4 a −11,2) |
| H2. Cuesta menos que la receta sin previsión | ✅ −344 $ por semana (IC: −406 a −283) |
| H3. Leer los correos reduce las semanas con la reserva rota | ✅ por los pelos: −0,4 puntos (IC: −0,8 a −0,0) |
| H4. Previsión calibrada (menos de 10 puntos de error en cada tramo) | ❌ 10,9 puntos |

### Por qué falló la H4 y qué se cambió
- **No era un fallo del simulador:** lo cobrado a tiempo (78,1 %) cuadra con la verdad oculta (78,0 %)
- **Era una debilidad del modelo:** encogía las estimaciones hacia la media con una fuerza fija de 4, que se quedaba corta. Con poco historial se creía demasiado la suerte de cada cliente
- **Arreglo:** el motor calcula esa fuerza a partir de la cartera (Bayes empírico: verosimilitud beta-binomial). Con la cartera de la demo sale 10,1. Pasan las 49 pruebas y la historia de la demo no cambia
- **Comprobado con una semilla nueva (2027)**, que no se había mirado. La semilla 2026 sirvió para diagnosticar, así que su repetición no cuenta como prueba

### Comprobación con el modelo arreglado (semilla 2027, la que no se había mirado) → **las cifras para el vídeo**
| Agente | Reserva rota | Pago imprescindible aplazado | Coste por semana | Pagado por semana |
|---|---|---|---|---|
| **Treasury Triage** | **0,2 %** | 13,0 % | **992 $** | 18.563 $ |
| Sin leer correos | 0,7 % | 12,2 % | 916 $ | 19.095 $ |
| Receta sin previsión | 0,0 % | 15,5 % | 1.290 $ | 15.734 $ |
| Pagar por orden | 15,2 % | 0,0 % | 118 $ | 22.564 $ |

- H1 ✅ (−15,0 puntos), H2 ✅ (−298 $ por semana), H3 ✅ (−0,5 puntos; IC −0,9 a −0,1)
- **H4 ❌ 11,5 puntos.** La nota global de la previsión sí mejora (Brier: 0,168 con correos, 0,181 solo con el historial y 0,185 con una tasa fija para todos), pero no llega al criterio por tramo
- Gráficas: `app/resultados/simulacion_1000_normales_semilla2027_agentes.png` y `..._calibracion.png`

### Clientes «fiables» (pagan a tiempo el 90 %; se añadió después de la prueba corta, sin hipótesis)
| Semilla 2027 | Reserva rota | Imprescindible aplazado | Coste por semana |
|---|---|---|---|
| **Treasury Triage** | **0,2 %** | 8,5 % | **621 $** |
| Sin leer correos | 0,3 % | 8,3 % | 604 $ |
| Receta sin previsión | 0,0 % | 14,6 % | 1.241 $ |
| Pagar por orden | 8,6 % | 0,0 % | 110 $ |

H1 ✅ y H2 ✅ (−619 $ por semana). H3 no se cumple: el efecto es casi nulo. H4 no se cumple (12,4 puntos).

## Qué significa, sin adornos
**Lo fuerte (se cumple en las dos semillas y con los dos tipos de cliente):**
- Frente a «pagar por orden», Treasury Triage **rompe la reserva entre 15 y 75 veces menos** (0,2-0,5 % de las semanas frente a 7-15 %)
- Frente a la receta oficial sin previsión, **cuesta 230-720 $ menos por semana**, paga entre 2.800 y 5.600 $ más de facturas por semana y aplaza menos veces el pago imprescindible
- Es decir: casi tan seguro como ir con miedo y bastante más barato. Saber cuánto fiarse de los cobros vale dinero

**Lo flojo (hay que saberlo y decirlo si el jurado pregunta):**
1. **Leer los correos aporta poco:** con clientes normales baja la reserva rota de 0,7 % a 0,2 %, pero cuesta unos 76 $ más por semana porque el agente se vuelve más prudente. Con clientes fiables no hay diferencia clara
2. **La calibración no es perfecta:** el peor tramo se desvía 11-12 puntos. Con solo 30 clientes, cada error de un cliente se repite en muchas semanas
3. ~~**Aprobaciones con clientes normales:** el agente pide aprobación en ~80 % de las semanas y esas semanas no son más arriesgadas que las demás.~~ **Arreglado en la segunda tanda** (ver abajo): ahora pide una persona en el 33 % de las semanas, y en esas semanas hay muchos más cobros tardíos
4. **Aplaza el pago imprescindible en 1 de cada 8 semanas** para no romper la reserva. Es una decisión de política: si el dueño prefiere no parar nunca, se baja la reserva o se sube la penalización
5. **Limitaciones:** los datos son sintéticos, y la forma de medir el coste es la del propio motor (juega en casa). La reserva rota no entra en el coste: por eso «pagar por orden» sale barato

## Para el vídeo (en inglés; primera versión, sustituida por la de la segunda tanda)
> "Across 1,000 simulated weeks, Treasury Triage broke the cash reserve in 0.2% of weeks, against 15.2% for paying bills in order, and cost $298 a week less than a rule-based agent with no forecast, while paying $2,800 more of the bills."

## Segunda tanda (27-oct, noche): aprobación «peor que lo normal»
**El cambio:** antes el agente pedía una persona según umbrales fijos (85 % y 60 % de confianza). Ahora la pide cuando la confianza de la semana cae por debajo de lo normal en esa cartera (la tasa habitual de cobro a tiempo):
- Con una caída de más de 5 puntos: límite reducido (2.500 $)
- Con una caída de más de 15 puntos, o con una confianza por debajo del 50 %: nada sin una persona

Hipótesis y umbrales fijados en el código antes de medir. Semilla nueva, 2028. En la simulación la aprobación no cambia las decisiones (se supone que la persona aprueba): solo se mide cuántas veces pide y si acierta el riesgo.

| Semilla 2028 | Regla | Semanas en que pide una persona | Algún cobro tarde si pide | Si no pide | Diferencia (IC 95 %) |
|---|---|---|---|---|---|
| Clientes normales | fija (antes) | 80,0 % | 46,2 % | 37,5 % | 8,8 puntos (1,2 a 16,3) |
| Clientes normales | **relativa (ahora)** | **33,0 %** | **60,6 %** | 36,6 % | **24,0 puntos (17,6 a 30,4)** |
| Clientes fiables | fija | 11,0 % | 34,5 % | 20,1 % | 14,4 puntos (5,2 a 23,7) |
| Clientes fiables | relativa | 19,8 % | 33,8 % | 18,7 % | 15,1 puntos (8,0 a 22,3) |

| Hipótesis | Resultado |
|---|---|
| H5. Con clientes normales pide una persona como mucho el 40 % de las semanas | ✅ 33 % (antes 80 %) |
| H6. Cuando pide, hay más cobros tardíos que cuando no pide | ✅ +24 puntos (antes +9) |
| H7. La demo no cambia (antes del correo convierte solo; después, las dos conversiones necesitan aprobación) | ✅ prueba automática. Ojo: con la bandeja entera, la confianza (75,5 %) queda solo medio punto por debajo del umbral (76,0 %) |

**Lo que hay que decir también:** con clientes fiables, la regla nueva pide una persona más a menudo que la fija (20 % frente a 11 %), con un acierto parecido.

**Resto de la semilla 2028 (clientes normales):**
- Reserva rota: 0,7 % frente al 11,9 % de pagar por orden
- Coste: 848 $ frente a 1.222 $ de la receta
- Las cuatro hipótesis H1-H4 se cumplen, y la calibración se queda en 8,1 puntos
- **Calibración: pasa en la semilla 2028 y falla en la 2027** (11,5). No es estable: con 30 clientes, el resultado depende mucho de qué clientes salgan

**Resumen de las tres semillas** (cada una, 1.000 semanas):
- Reserva rota: 0,2-0,7 % de las semanas, frente al 12-15 % de pagar por orden
- Coste: entre 230 y 374 $ menos por semana que la receta sin previsión
- Pagado: entre 2.800 y 3.400 $ más de facturas por semana

## Para el vídeo (en inglés, segunda versión, sustituida por la de la tercera tanda)
> "Across three independent runs of 1,000 simulated weeks, Treasury Triage broke the cash reserve in under 1% of weeks, against 12-15% for paying bills in order, and cost $230-$370 a week less than a rule-based agent with no forecast. It asks a person in a third of the weeks, and those are the weeks when payments really do run late: 61% of them, against 37% of the rest."

## Tercera tanda (28-oct, tarde): el agente aprende de cada cobro
**El problema:** la previsión se quedaba con el historial inicial las 1.000 semanas: nunca aprendía de los cobros que iban llegando. Con 30 clientes, el error de cada uno se repetía semana tras semana.

**Diagnóstico** con las semillas ya usadas (`app/diagnostico_prevision.py`, solo previsiones). Peor tramo, en puntos:
| Semilla | Ahora | El historial crece | Autocontrol | Los dos | La verdad oculta |
|---|---|---|---|---|---|
| 2026, normales | 6,9 | 8,9 | 9,5 | **3,7** | 9,9 |
| 2027, normales | 11,5 | 7,8 | 6,5 | **6,0** | 9,2 |
| 2028, normales | 8,1 | 6,8 | 8,0 | 8,3 | 10,5 |
| 2027, fiables | 12,4 | 4,2 | 3,0 | **1,3** | 6,0 |
| 2028, fiables | 9,8 | 3,4 | 5,3 | **2,1** | 5,6 |

- «Los dos» tiene el mejor Brier en las cinco: con clientes normales baja de 0,149-0,168 a 0,144-0,155 (la verdad oculta da 0,141-0,151). Se elige ese
- **La H4 es ruidosa:** hasta la probabilidad verdadera la falla en la semilla 2028 (10,5), porque hay tramos con solo 50-100 cobros. Por eso se añade la H9, que no depende de tramos

**El cambio** (clase `Aprendiz` en `app/simulacion.py`):
- Cada cobro entra en el historial de su cliente 4 semanas después, cuando ya han llegado hasta los tardíos. Si entrara al llegar, el historial reciente tendría de más los que pagan a tiempo
- Cada 20 semanas, el autocontrol del motor común (el mismo que usa PayPal) compara lo que creyó con lo que pasó y corrige la previsión base y lo que vale un correo
- En la demo no cambia nada: la empresa empieza hoy con el agente y no tiene trayectoria (lo comprueba la prueba de la H7)
- Comprobado antes de medir: el agente «sin aprender» da exactamente lo de antes en la semilla 2027 (0,2 % y 992 $)

**Hipótesis** fijadas en el código antes de medir, con dos semillas nuevas (2029 y 2030) y clientes normales. Cada una tiene que cumplirse en las dos:
- H8. El Brier del agente que aprende es menor que el del que no aprende (IC 95 % por debajo de 0)
- H9. Cierra al menos la mitad de la distancia (en Brier) entre la previsión sin aprender y la verdad oculta
- H10. No es más arriesgado: la diferencia de semanas con la reserva rota frente al agente sin aprender tiene el extremo superior del IC 95 % en +0,5 puntos o menos
- H11. Cuesta menos por semana que el agente sin aprender (IC 95 % por debajo de 0)
- La H4 se vuelve a medir con el agente que aprende

**Resultados:**
| | Semilla 2029 | Semilla 2030 |
|---|---|---|
| Reserva rota: aprende / sin aprender / pagar por orden | 0,7 % / 0,3 % / 12,4 % | 0,6 % / 0,8 % / 10,6 % |
| Pago imprescindible aplazado: aprende / sin aprender | 8,9 % / 11,1 % | 11,7 % / 13,4 % |
| Coste por semana: aprende / sin aprender / receta | 709 / 890 / 1.248 $ | 825 / 922 / 1.299 $ |
| Pagado por semana: aprende / sin aprender | 19.717 / 18.482 $ | 19.609 / 19.267 $ |
| Peor tramo: aprende / sin aprender / verdad oculta | 6,0 / 11,3 / 3,5 | 5,5 / 4,6 / 6,4 |
| Brier: aprende / sin aprender / verdad oculta | 0,154 / 0,165 / 0,150 | 0,139 / 0,143 / 0,135 |
| Semanas en que pide una persona | 20,6 % | 29,7 % |
| Algún cobro tarde si pide / si no pide | 71,4 % / 44,8 % | 65,3 % / 34,3 % |

| Hipótesis | Semilla 2029 | Semilla 2030 |
|---|---|---|
| H4. Calibración: peor tramo a menos de 10 puntos | ✅ 6,0 | ✅ 5,5 |
| H8. Brier mejor que sin aprender | ✅ −0,011 (IC −0,015 a −0,007) | ✅ −0,004 (IC −0,006 a −0,002) |
| H9. Cierra al menos la mitad de la distancia a la verdad | ✅ 76 % | ✅ 56 % |
| H10. No más arriesgado (extremo superior ≤ +0,5 puntos) | ❌ +0,4 puntos (IC +0,0 a +0,8) | ✅ −0,2 (IC −0,6 a +0,2) |
| H11. Más barato que sin aprender | ✅ −181 $ (IC −228 a −133) | ✅ −97 $ (IC −144 a −50) |
| H1, H2, H5 y H6 | ✅ | ✅ |
| H3. Leer los correos rompe menos la reserva | ❌ −0,2 (IC −0,7 a +0,3) | ✅ −0,4 (IC −0,8 a −0,0) |

Clientes fiables, semilla 2029 (sin hipótesis): Brier 0,079 frente a 0,082; peor tramo 8,0 frente a 7,9; coste 549 $ frente a 587 $ (IC −86 a +11: no está claro); reserva rota igual (0,3 %).

**Qué significa, sin adornos:**
- **La previsión mejora de verdad:** pasa la calibración en las dos semillas y cierra entre la mitad y tres cuartos de la distancia a la previsión perfecta
- **Con una previsión mejor, el agente se fía más de los cobros:** paga más facturas (entre 340 y 1.240 $ más por semana), aplaza menos el pago imprescindible y cuesta entre 97 y 181 $ menos por semana
- **El precio:** en la semilla 2029 rompe la reserva 4 semanas más de cada 1.000 (0,7 % frente a 0,3 %). La H10 falla y «no es más arriesgado» no está demostrado. Sigue lejos del 11-12 % de pagar por orden
- En la 2030, la calibración sin aprender ya pasaba (4,6) y aprendiendo queda en 5,5: ahí la mejora está en el Brier, no en el peor tramo
- Leer los correos sigue aportando poco: ayuda en una semilla y en la otra no está claro
- Las gráficas son de la primera semilla nueva, la 2029 (`app/resultados/simulacion_1000_normales_semilla2029_*.png`)

## Para el vídeo (en inglés, versión de la tercera tanda, sustituida por la de la cuarta)
> "We ran a thousand simulated weeks against simpler agents, on two fresh runs. Treasury Triage broke the reserve in under one percent of weeks; paying bills in order broke it in eleven to twelve percent. It cost $470-$540 a week less than an agent with no forecast. It learns from every payment, so its forecast stays within six points of what really happens. Not everything worked: on one run, learning made it break the reserve a little more often, and we report that too."

## Experimento (28-oct, noche): ¿pagar con la reserva el pago que para la empresa? → el candidato dijo que sí
**¿Sabe el agente cuánto arriesga?** Sí, y va algo prudente: en la semilla 2027 prevé romper la reserva en el 0,9 % de las semanas y la rompe en el 0,3 % (sin aprender: 1,1 % frente a 0,2 %). Ahí no hay mucho que arreglar.

**Lo que sí pesa:** dos tercios de su coste (unos 480-630 $ de los 709-825 $ por semana de las semillas 2029 y 2030) vienen de las semanas en que aplaza el pago que para la empresa (el transporte) para no tocar la reserva.

**La opción** (`TreasuryPolicy.reserve_covers_must_pay`, apagada): en esas semanas puede planear pagar SOLO ese pago con la reserva, nunca por debajo de cero, y una persona lo aprueba. Semilla ya usada 2027 (`app/experimento_reserva.py`; se supone que la persona aprueba):

| | Como ahora | Con la opción |
|---|---|---|
| Semanas con la empresa parada | 10,6 % | 0,0 % |
| Semanas en que se toca la reserva | 0,3 % | 5,7 % (siempre con una persona) |
| Coste por semana | 838 $ | 357 $ |
| Pagado por semana | 19.337 $ | 19.569 $ |

Pagar por orden rompe la reserva en el 15,2 % de las semanas. Era una decisión de política: el candidato dijo que sí («Sí, usa la reserva») y se midió en la cuarta tanda.

## Cuarta tanda (28-oct, noche): la reserva paga el pago que para la empresa, con una persona
**El cambio** (ya es la regla por defecto, `TreasuryPolicy.reserve_covers_must_pay`): si no tocar la reserva obliga a aplazar el pago que para la empresa, el plan paga SOLO ese pago con la reserva, siempre que la caja no baje de cero (con la misma probabilidad máxima del 5 %). En el producto, ese pago no sale sin una tarjeta aprobada (`aprobaciones.aprobacion_de_reserva`; 2 pruebas nuevas). En la simulación se supone que la persona aprueba. La demo no cambia: ahí no hace falta tocar la reserva.

**Hipótesis** fijadas en el código antes de medir, semillas nuevas 2031 y 2032, clientes normales. Agente de comparación: «Treasury Triage sin tocar la reserva» (el de la tercera tanda).

| Hipótesis | Semilla 2031 | Semilla 2032 |
|---|---|---|
| H12. La empresa se para en menos del 1 % de las semanas | ✅ 0,0 % (sin tocar la reserva: 11,5 %) | ✅ 0,0 % (12,6 %) |
| H13. Más barato que sin tocar la reserva | ✅ −526 $ por semana (IC −617 a −435) | ✅ −567 $ (IC −660 a −474) |
| H14. Reserva rota en menos del 10 %, y menos que pagando por orden | ✅ 5,4 % frente a 13,7 % | ✅ 6,6 % frente a 13,6 % |
| H15. Caja por debajo de cero en menos del 1 % | ✅ 0,0 % | ✅ 0,0 % |
| H1, H2, H4, H5, H6, H8, H9, H11 | ✅ | ✅ |
| H3. Leer los correos rompe menos la reserva | ❌ +0,2 (IC −0,2 a +0,6) | ❌ −0,1 (IC −0,4 a +0,2) |
| H10. Aprender no es más arriesgado | ✅ 0,0 | ❌ +0,4 puntos (IC +0,0 a +0,8) |

| | Semilla 2031 | Semilla 2032 |
|---|---|---|
| Treasury Triage: reserva rota / empresa parada / coste por semana | 5,4 % / 0,0 % / 321 $ | 6,6 % / 0,0 % / 351 $ |
| Sin tocar la reserva | 1,3 % / 11,5 % / 847 $ | 0,9 % / 12,6 % / 918 $ |
| Receta sin previsión | 0,0 % / 15,2 % / 1.258 $ | 0,0 % / 16,0 % / 1.299 $ |
| Pagar por orden | 13,7 % / 0,0 % / 105 $ | 13,6 % / 0,0 % / 112 $ |
| Semanas en que el plan pide usar la reserva | 11,5 % | 12,6 % |
| Riesgo de romper la reserva: previsto / real | 5,0 % / 5,4 % | 6,2 % / 6,6 % |
| Calibración: peor tramo / Brier frente a sin aprender | 4,2 / 0,165 frente a 0,169 | 4,1 / 0,161 frente a 0,173 |
| Pide una persona para el cambio de divisa / algún cobro tarde si pide o no | 24,3 % / 63,4 % frente a 47,4 % | 22,2 % / 73,9 % frente a 48,3 % |

Clientes fiables, semilla 2031 (sin hipótesis): la empresa nunca se para (sin tocar la reserva, 8,0 %), cuesta 222 $ frente a 581 $ y la reserva se rompe en el 4,0 %. Aquí la calibración se desvía 11,3 puntos en un tramo (sin aprender, 1,1).

**Qué significa, sin adornos:**
- **La empresa deja de pararse** y el coste baja a menos de la mitad (de 850-920 $ a 320-350 $ por semana), a cambio de bajar de la reserva en el 5-7 % de las semanas, siempre con una persona y sin bajar nunca de cero
- **El plan pide usar la reserva el doble de veces de las que hace falta** (11-13 % de las semanas frente a 5-7 %): muchas veces el cobro llega a tiempo y la reserva no se llega a tocar
- **El agente sabe cuánto arriesga:** prevé romper la reserva en el 5,0-6,2 % de las semanas y pasa en el 5,4-6,6 %
- Leer los correos sigue sin aportar algo claro, y aprender vuelve a hacerlo un poco más atrevido en una semilla (+0,4 puntos)

## Para el vídeo (en inglés, versión final de la cuarta tanda)
> "We ran a thousand simulated weeks against simpler agents, on two fresh runs. Treasury Triage never stopped the business. When keeping the reserve would have meant deferring the freight, it asked a person to use the reserve instead, and cash never went below zero. It cost about $940 a week less than an agent with no forecast, and it learns from every payment, so its forecast stays within six points of what really happens. The price: it dipped into the reserve in about six percent of weeks, against fourteen percent when paying bills in order."

## El techo (28-oct, noche): ¿cuánto ganaría con una previsión perfecta?
`app/techo_oraculo.py` (no cuenta como prueba; semillas ya medidas). El mismo motor y la misma política, con la previsión perfecta: la probabilidad verdadera de cada cliente, el correo bien usado (que no escriba también es una pista) y cómo se reparten de verdad sus retrasos. No sabe la suerte de la semana.

| Semilla | Previsión perfecta: reserva rota / parada / coste | Treasury Triage |
|---|---|---|
| 2031 | 5,4 % / 0,0 % / 322 $ | 5,4 % / 0,0 % / 321 $ |
| 2032 | 6,5 % / 0,0 % / 360 $ | 6,6 % / 0,0 % / 351 $ |

**Qué significa:** con la previsión perfecta decidiría lo mismo y costaría lo mismo. **La previsión de Airwallex ya está en su techo:** seguir afinándola no aporta nada medible. Lo que queda por mejorar no es el modelo, sino la política (cuánta reserva, cuándo pedir una persona) y el producto.
