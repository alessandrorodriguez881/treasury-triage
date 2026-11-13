# How Treasury Triage meets the Airwallex builder guide

The [Airwallex builder guide](https://airwallexdev.com/guide) asks every project to be built around a financial decision, in nine points. This page maps each point to the code that does it, the test that proves it and where to see it in the [live demo](https://treasury-triage.streamlit.app). Every link opens the exact line.

File, function and variable names are in Spanish, the author's language. What the user sees, the tests and this page are in English, and there is a [glossary](#glossary-the-spanish-names) at the end.

A test, [`test_every_link_on_the_guide_map_points_at_what_it_names`](../tests/test_mapa_guia.py#L11), checks that every code and test link on this page still points at the function or test it names, so the map cannot drift from the code.

## Kit 01: what the agent decides

Five bills are due in the next 72 hours, in USD, EUR and GBP, and there is not enough cash to pay them all without breaking the $15,000 reserve. For each bill the engine picks one of four actions: fund, convert and fund, defer, or escalate to a person. It takes the cheapest plan whose chance of breaking the reserve, over 5,000 simulated cash paths, stays under 5%.

- **Code:** [`plan_treasury`](../motor_comun/triage_core/treasury_decision.py#L89) · [`simulate`](../motor_comun/triage_core/cash_simulation.py#L45) · [`TreasuryPolicy`](../motor_comun/triage_core/policy.py#L8)
- **Test:** [`test_plan_funds_critical_defers_what_frees_cash_escalates_exception`](../motor_comun/tests/test_core.py#L91)

## The nine points of the guide

### 1. A repeated financial task, with a decision that changes when a balance, a deadline, the evidence or a risk changes

- **How:** every round reads the balances and the FX rate from Airwallex again and plans again. An email saying a customer will pay late lowers the forecast, and with it the agent's FX limit. A customer's deposit reopens only the decisions the new cash changes, and the screen shows that difference.
- **Code:** [`situacion`](../demo.py#L86) · [`conciliar`](../demo.py#L342) · [`diff_plans`](../motor_comun/triage_core/treasury_decision.py#L163)
- **Tests:** [`test_late_customer_risk_changes_the_plan`](../motor_comun/tests/test_core.py#L107) · [`test_replan_after_deposit_only_reports_changes`](../motor_comun/tests/test_core.py#L131) · [`test_globex_payment_only_changes_the_saas_and_keeps_the_reserve`](../tests/test_escenario.py#L69) · [`test_another_scenario_shows_every_kind_of_change`](../tests/test_otro_escenario.py#L49)
- **In the demo:** step 2 (confidence 87% → 75%, FX limit $10,000 → $2,500) and step 4 (Globex pays, and only the SaaS bill changes: defer → pay). The **What if?** tab does the same for any other week.

### 2. The agent moves money, and it is clear which actions need a person

- **How:** it books FX conversions at a locked quote and sends supplier transfers on Airwallex until they are PAID. A person approves three kinds of action: a conversion above the agent's FX limit, a payment from the reserve, and any bill the plan escalates (a new payee, or an invoice that does not match).
- **Code:** [`convertir`](../demo.py#L401) · [`pagar`](../demo.py#L498) · [`autonomous_fx_limit`](../motor_comun/triage_core/policy.py#L30) · [`aprobacion_de_conversion`](../aprobaciones.py#L119) · [`aprobacion_de_reserva`](../aprobaciones.py#L149)
- **Tests:** [`test_relative_autonomy_asks_a_person_only_when_this_week_is_worse_than_usual`](../motor_comun/tests/test_core.py#L143) · [`test_email_makes_both_conversions_wait_for_a_person`](../tests/test_escenario.py#L49) · [`test_a_bill_paid_from_the_reserve_never_leaves_without_a_person`](../tests/test_demo.py#L124)
- **In the demo:** step 3 (the freight bill is PAID, and two approval cards wait for you), and rules R4, R5 and R7 on the **How it decides** tab.

### 3. The demo shows the user's goal, the first plan, the new information, the revised decision and the result

- **How:** the five moments sit under the title, and the one on screen is marked as you press the steps.
- **Code:** [`PASOS_KIT`](../panel.py#L288) · [`paso_siguiente`](../demo.py#L105)
- **Test:** [`test_the_next_step_follows_the_order_of_the_demo`](../tests/test_demo.py#L143)
- **In the demo:** the goal is to pay the week's bills without breaking the $15,000 reserve. The plan is the opening screen; the new information comes in step 2 (emails and invoices) and step 4 (Globex's payment); the revised decision in steps 3 and 4; the result is the green **Reserve intact** banner.

### 4. Amounts, limits, reserves, validations, duplicate protection and state changes live in code; the model reads, compares, proposes and explains

- **How:** the numbers (the reserve, the risk limit, the FX limits, the fees and what each kind of email can change) live in one policy file. Claude's readings count only after code validates them. Every money movement goes through a ledger with one `request_id` per operation, a paid bill cannot be paid again, and each change to the ledger is chained into a hash-linked audit trail. Claude reads the invoices and emails, and writes the owner's briefing.
- **Code:** [`TreasuryPolicy`](../motor_comun/triage_core/policy.py#L8) · [`validate`](../motor_comun/triage_core/signals.py#L38) · [`validate_reading`](../motor_comun/triage_core/invoices.py#L72) · [`begin`](../motor_comun/triage_core/ledger.py#L129) · [`verify`](../motor_comun/triage_core/ledger.py#L87)
- **Tests:** [`test_retry_reuses_request_id_and_no_double_payment`](../motor_comun/tests/test_core.py#L207) · [`test_a_reading_counts_only_if_every_value_is_on_the_invoice`](../motor_comun/tests/test_invoices.py#L28) · [`test_recompute_uses_only_accepted_signals_and_invoices`](../tests/test_agente_ia.py#L79) · [`test_the_rules_never_move_whatever_the_week`](../tests/test_otro_escenario.py#L34) · [`test_an_event_changed_by_hand_fails_the_check`](../tests/test_verificar_auditoria.py#L36)
- **In the demo:** the **How it decides** tab (rules R1 to R11) and the **Audit trail** tab, where you can download the trail and run the same check on your own computer.

### 5. Typed tools for the model, never unbounded credentials

- **How:** Claude has five tools, built from typed Python signatures (`record_signal` accepts only four kinds of signal). None of them moves money. Claude never sees an API key or an Airwallex id: the plan it reads has neither.
- **Code:** [`Bandeja`](../agente_ia.py#L63) · [`record_invoice`](../agente_ia.py#L88) · [`record_signal`](../agente_ia.py#L121) · [`recompute_plan`](../agente_ia.py#L142) · [`leer_bandeja`](../agente_ia.py#L167) · [`resumen`](../demo.py#L59)
- **Tests:** [`test_the_fake_cfo_email_cannot_become_a_payment_or_a_signal`](../tests/test_agente_ia.py#L34) · [`test_an_invented_quote_is_rejected_and_the_reason_goes_back_to_claude`](../tests/test_agente_ia.py#L47) · [`test_the_prompt_forbids_obeying_emails_and_fixing_invoices`](../tests/test_agente_ia.py#L92)
- **In the demo:** step 2, under **What the agent read**: each tool call, with the code's answer.

### 6. Every approval is bound to the amount, currency, counterparty, conditions and evidence the approver saw

- **How:** an approval request holds the action, the exact amount of an Airwallex quote, the currency, the counterparty, the reason and the evidence: the quote, the round and the emails behind it. Its fingerprint covers the action, amount, currency, counterparty and evidence. The conversion runs only at the approved quote, while that quote is valid and for the approved amount. A new amount, an expired quote or a new email needs a new card.
- **Code:** [`ApprovalRequest`](../motor_comun/triage_core/approvals.py#L17) · [`fingerprint`](../motor_comun/triage_core/approvals.py#L26) · [`aprobacion_de_conversion`](../aprobaciones.py#L119)
- **Tests:** [`test_approval_is_bound_to_exact_terms`](../motor_comun/tests/test_core.py#L152) · [`test_approved_card_converts_at_its_quote`](../tests/test_aprobaciones.py#L41) · [`test_other_amount_or_a_new_signal_needs_a_new_approval`](../tests/test_aprobaciones.py#L48) · [`test_expired_quote_needs_a_new_approval`](../tests/test_aprobaciones.py#L56)
- **In the demo:** the approval cards after step 3, with "Rate locked by Airwallex quote", the reason and the emails.

### 7. Sandbox calls sit behind one interface

- **How:** every Airwallex call goes through one wrapper. In demo mode a simulator plugs in underneath it, as its HTTP session, and answers the same REST calls, so the agent's code is the same with and without keys.
- **Code:** [`AirwallexRail`](../rail_airwallex.py#L40) · [`call`](../rail_airwallex.py#L69) · [`SandboxSimulado`](../simulador_airwallex.py#L56) · [`conectar`](../modo.py#L77)
- **Tests:** [`test_it_behaves_like_the_sandbox_where_it_matters`](../tests/test_simulador.py#L67) · [`test_the_whole_demo_runs_without_keys`](../tests/test_simulador.py#L42) · [`test_retry_resends_the_same_request_id`](../tests/test_rail.py#L49)
- **In the demo:** the badge next to the title: "Simulated Airwallex sandbox · no keys" on the live demo, "Live on the Airwallex sandbox" with keys.

### 8. The demo ends with a financial action or an escalation to a person

- **How:** the round ends with every bill the agent may pay PAID on Airwallex and the reserve checked, and with the new supplier escalated to the owner, who can reject it with its reason.
- **Code:** [`pagar`](../demo.py#L498) · [`rechazar`](../control.py#L65) · [`plan_treasury`](../motor_comun/triage_core/treasury_decision.py#L89)
- **Tests:** [`test_plan_funds_critical_defers_what_frees_cash_escalates_exception`](../motor_comun/tests/test_core.py#L91) · [`test_a_rejected_bill_leaves_the_plan_with_its_reason`](../tests/test_control.py#L60)
- **In the demo:** the last step 3 ("All bills the agent may pay are paid") and the **Reject** button on Q. Consulting's card.

### 9. A decision that is always the same is a rule in code, not a question for the model

- **How:** fixed decisions are numbered rules, R1 to R11, and each decision card cites the one it follows. Three examples: a new payee always goes to a person (R7); a payment always goes to the beneficiary on file, never to an account read from an invoice (R8); an email can move only that customer's odds, by a factor fixed in the policy (R9).
- **Code:** [`REGLAS`](../panel.py#L108) · [`beneficiario`](../demo.py#L385) · [`SIGNAL_LIKELIHOOD`](../motor_comun/triage_core/policy.py#L57) · [`apply_signals`](../motor_comun/triage_core/signals.py#L57)
- **Tests:** [`test_changed_bank_details_send_the_bill_to_a_person`](../motor_comun/tests/test_invoices.py#L69) · [`test_signals_only_move_their_own_customer`](../motor_comun/tests/test_core.py#L198) · [`test_a_signal_counts_only_with_verbatim_evidence_from_the_customers_own_domain`](../motor_comun/tests/test_core.py#L180)
- **In the demo:** the **How it decides** tab, and the "R… ·" line on each card of the **Today** tab.

## Glossary: the Spanish names

| File | What it is |
|---|---|
| `panel.py` | The screen (Streamlit) |
| `demo.py` | The demo's steps: read the inbox, convert, pay, reconcile |
| `escenario.py` | The demo's week: bills, customers, inbox and supplier files |
| `agente_ia.py` | Claude's tools and prompt |
| `aprobaciones.py`, `aprobar.py` | Approval cards (on screen, and from the console) |
| `control.py` | The owner's controls: pause, resume, reject |
| `rail_airwallex.py` | The Airwallex API wrapper |
| `simulador_airwallex.py`, `modo.py` | The simulator for demo mode, and the switch between it and the real sandbox |
| `comprobacion_metal.py`, `rail_metal.py`, `metal/` | The payee check paid on Metal testnet |
| `otro_escenario.py` | The **What if?** tab |
| `verificar_auditoria.py` | The audit-trail check |
| `ejecutar_plan.py` | The same steps from the console |
| `simulacion.py`, `graficas.py` | The 1,000-week evaluation and its charts |
| `experimento_reserva.py`, `techo_oraculo.py`, `diagnostico_prevision.py` | Extra experiments on seeds already used (not counted as evidence) |
| `preparar_sandbox.py`, `ajustar_saldos_demo.py`, `reiniciar_demo.py`, `probar_airwallex.py` | Set up, reset and test the sandbox |
| `recibo_sandbox.py`, `evidencia/` | Receipts of real sandbox rounds |
| `motor_comun/triage_core/` | The decision engine (names in English) |

Words that come up in the code: *ronda* round · *saldo* balance · *factura* invoice · *cobro* money coming in · *pago* payment · *libro* ledger · *aprobar* / *rechazar* approve / reject · *señal* what an email says about paying · *bandeja* inbox · *reserva* reserve · *huella* fingerprint · *grabacion* recording · *situacion* the position: balances, rates and plan.
