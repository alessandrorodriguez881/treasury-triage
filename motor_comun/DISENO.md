# Motor común de Treasury Triage (Airwallex) y Receivables Copilot (PayPal): diseño técnico

Escrito el 26-oct-2026. **El código se empieza el mar 27-oct**, cuando arranca el desarrollo de Airwallex.

## La idea en una frase
Un solo «cerebro» que estima **quién va a pagar y cuándo**, decide **por dinero esperado** y pide **aprobación** cuando no está seguro. Cada hackathon lo enchufa a la plataforma de su patrocinador.

```
              ┌──────────────── motor común (Python) ────────────────┐
datos ──────► │ 1. Probabilidad de cobro por cliente (historial)     │
correos ────► │ 2. Simulación de caja (muchos escenarios)            │
facturas ───► │ 3. Regla de decisión por coste esperado + política   │
              │ 4. Aprobaciones atadas a importe/divisa/contraparte  │
              │ 5. Libro de movimientos + protección de duplicados   │
              │ 6. Capa de IA (Claude): lee texto, explica, redacta  │
              └────────────┬─────────────────────────┬───────────────┘
                           │                         │
              rail Airwallex (pagar)       rail PayPal (cobrar)
              Treasury Triage              Receivables Copilot
```

## Las piezas
| # | Pieza | Qué hace | Va en código o en la IA |
|---|---|---|---|
| 1 | **Modelo de cobro** | Probabilidad de que cada cliente pague a tiempo y retraso esperado, a partir de su historial. Se encoge hacia la media cuando hay pocos datos | Código (estadística) |
| 2 | **Simulación de caja** | Miles de escenarios de los próximos días con esas probabilidades → probabilidad de bajar de la reserva | Código |
| 3 | **Regla de decisión** | Elige la acción con menor coste esperado que cumpla la política (reserva, límites) | Código |
| 4 | **Aprobaciones** | Si la confianza baja o se supera un límite, crea una solicitud atada al importe, la divisa, la contraparte y las pruebas. Si algo cambia, la aprobación caduca | Código |
| 5 | **Libro y duplicados** | Registra cada operación con su `request_id`; un reintento nunca paga ni cobra dos veces | Código |
| 6 | **Capa de IA** | Claude con herramientas tipadas: extraer datos de facturas y correos, detectar contradicciones, explicar decisiones, redactar recordatorios. **Nunca toca claves ni importes** | IA |
| 7 | **Rail** (uno por patrocinador) | Una sola interfaz con las llamadas a la plataforma, incluidas las de simulación del sandbox | Código |

## Qué cambia en cada producto
| | Treasury Triage (Airwallex) | Receivables Copilot (PayPal) |
|---|---|---|
| Para quién | Fundador de una pyme que vende en varios países | Autónomo o pyme que cobra con facturas de PayPal |
| Decisión | Pagar, convertir divisa, aplazar o escalar una factura **que debes** | Esperar, recordar, ofrecer descuento o plazos, o escalar una factura **que te deben** |
| Información nueva | Correo del cliente que contradice la previsión; depósito que llega | Respuesta del cliente («pago el viernes»); pago que llega (aviso de PayPal) |
| Acción de dinero | Conversión de divisa + transferencia al proveedor | Recordatorio, descuento o plazos sobre la factura; registro del cobro |
| Interfaz | Sencilla (Streamlit); pesa la lógica | **Cuidada:** el diseño es el 20 % de la nota |
| Vídeo | Menos de 5 min | **Menos de 3 min** en YouTube |

## Decisiones técnicas
- **Lenguaje:** Python. Interfaz con Streamlit; si para PayPal hace falta más diseño, se decide en la semana 2
- **Datos:** sintéticos, con una semilla fija para que la demo salga siempre igual
- **Pruebas:**
  - Cada regla se prueba con casos conocidos antes de enchufar las plataformas
  - Las llamadas al sandbox se prueban una a una el día 1
- **Claves:** en un `.env` local que nunca se sube. Las de Airwallex y PayPal las crea y guarda el candidato
- **IA:** Claude por su API. Antes de programar esta parte se lee la guía de la API de Claude (modelo, herramientas)
  - **Pendiente:** con qué clave se paga. Si Airwallex nos acepta, con sus créditos de Claude; si no, lo decide el candidato
- **Repositorios:** **dos públicos**, uno por producto, cada uno con el motor dentro y licencia MIT
  - Así cada jurado ve solo su plataforma
  - Los crea el candidato en su GitHub (o yo, con su sí, porque publicar es una acción hacia fuera)
- **Dónde vive el código mientras tanto:** `hackathons/motor_comun/` (motor), `airwallex_2026/app/` y `paypal_2026/app/`

## Orden de construcción (lo detalla cada `PLAN.md`)
1. **Mar 27-mié 28:** piezas 1-5 con pruebas, sin plataformas
2. **Jue 29-lun 2:** rail de Airwallex + Treasury Triage de punta a punta (**hito el 3-nov**)
3. **Lun 2-mar 3:** rail de PayPal (facturas de prueba en el sandbox)
4. **Mié 4-dom 8:** Receivables Copilot + interfaz cuidada
5. **Lun 9:** funciones congeladas en los dos

## Riesgos y qué hacemos
| Riesgo | Qué hacemos |
|---|---|
| Airwallex no nos acepta | Solo PayPal; el motor sirve igual |
| Falta cuota de Claude (Name That LLM entrena desde el 31-oct) | Bloques cortos; si la ventana pasa del 70 %, solo lo imprescindible |
| La semana de DataMad (desde el 7-nov) | Lo pesado de los dos, antes del 7-nov |
| El sandbox de PayPal no dispara el aviso de pago | Registrar el pago con su API y leer el estado (plan B) |
