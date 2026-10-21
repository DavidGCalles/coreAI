# ADR-006: Contratos de Tarea MCP — Validación Síncrona en la Frontera y Exposición como Resources

## Status
Proposed

## Context and Problem Statement
El tracer bullet del ADR-004 validó la pasarela de ingesta (`coreai_dispatch_task` → tabla `tasks` → worker), pero el evento de diagnóstico del 18 de octubre de 2026 evidenció el problema estructural que el tracer no podía cubrir (**BUG-002**, #35): los clientes de borde — agentes LLM como Cline — no son capaces de respetar de forma fiable un contrato genérico y anidable. `task_payload` es hoy un `dict[str, Any]` libre y el handler de `VECTORIZE_EVENT` absorbe el caos con extracción resiliente (claves candidatas `content`/`summary_content`/`text`/`data`) — cinta adhesiva que enmascara el problema en vez de resolverlo.

El daño es doble: **(1) feedback loop del borde roto** — un payload que viola el contrato recibe una respuesta de éxito (`task_id`, `PENDING`) y muere minutos después en el worker, con la traza enterrada en `payload.error_trace` que el agente jamás volverá a ver; **(2) poison pills para la cola** — una violación de contrato es determinista, así que cuando el Issue 4.4 (retries/Exponential Backoff/DLQ) aterrice, cualquier validación que viva solo en el handler convertirá la confusión del cliente en basura previsible que revienta los reintentos.

Además, el Issue 4.3 requiere formalizar el contrato de `vectorize_event`; cerrarlo con el contrato implícito en el handler supondría construirlo una segunda vez con riesgo de drift. ELIOS, como primer cliente del sustrato, necesita descubrir los contratos en runtime en lugar de hardcodear formas de payload.

**Pregunta:** ¿dónde vive el contrato de un tipo de tarea, quién lo valida, y cómo lo descubre el cliente — sin fragmentar la única apertura de ingesta ni hinchar el manifiesto de tools?

## Decision Outcome
Contratos de tarea como **MCP resources** más **validación síncrona en la frontera del dispatch**. Cada `TaskType` del catálogo cerrado actual lleva un contrato de payload formal (modelo Pydantic) registrado en un registro central de contratos — la única fuente de verdad — que expone `validate(task_type, payload)`, `schema(task_type)` y `list()`; ningún tipo es despachable sin contrato registrado. El registro se publica vía `resources/list` y `resources/read` (recurso catálogo `coreai://contracts/tasks` y template `coreai://contracts/tasks/{task_type}`), servido desde el mismo objeto que valida: cero duplicación entre lo que el cliente lee y lo que el servidor aplica.

`coreai_dispatch_task` valida el `task_payload` contra el contrato del tipo dentro de la propia tool call, antes de la auto-vivificación de sesión/entidad; una violación devuelve `isError=True` con el error de Pydantic formateado y el contrato referenciado (URI del resource + esquema compacta inline). *Refinamiento del ADR-005:* la frontera se divide — la **forma** del payload se decide en el dispatch (visible para el cliente); la **semántica** permanece en el handler como backstop de defensa en profundidad.

Los fallos de contrato constituyen una **clase de fallo no reinteligible**: la taxonomía para el Issue 4.4 queda fijada aquí (fallos transitorios → reintento con backoff; fallos de contrato → imposibles en la cola por diseño, pre-filtrados en la frontera; violación residual detectada por el backstop del handler → `FAILED` sin consumir reintentos, dead-letter inmediato).

Vallas de alcance: `TaskType` cerrado por ahora (sin decidir la apertura de catálogos), sin almacenamiento multimodal, y sin nuevas superficies de tools — `coreai_dispatch_task` sigue siendo la única apertura de escritura y el recall del ADR-005 la única lectura síncrona.

### Positive Consequences
*   **Feedback loop del borde cerrado.** El error es visible en la misma llamada; el agente se autocorrige en el mismo loop (~2 roundtrips extra por tipo nuevo, conocimiento cacheable por el cliente).
*   **Cola sana por construcción.** Los reintentos del Issue 4.4 solo verán fallos transitorios; los poison pills de contrato desaparecen en origen.
*   **Manifiesto mínimo y estable.** Los contratos evolucionan sin hinchar el manifiesto de tools ni el contexto permanente de los agentes; escala con catálogos y arrendatarios sin churn.
*   **Sustrato que se describe a sí mismo.** Contratos machine-readable descubribles → menos drift cliente-sustrato (ELIOS los lee en runtime).
*   **Retirada del duct tape del 4.3.** El contrato formal de `vectorize_event` sustituye la extracción resiliente con claves candidatas; materializa [ADR-005-003].

### Negative Consequences
*   **Disciplina obligatoria.** Un tipo sin contrato registrado no es despachable; cada alta de tipo cuesta un contrato explícito.
*   **Coste de roundtrips.** Los clientes que no cachean contratos pagan un roundtrip extra en el primer uso de cada tipo (acotado y voluntario).
*   **Dependencia del path de error inline.** Clientes MCP con soporte pobre de resources dependen del error inline para autocorregirse; el resource queda como camino óptimo, no obligatorio.
*   **Dos capas de validación vivas.** Frontera (forma) + handler (semántica) coexisten permanentemente; se asume como defensa en profundidad, con su coste de mantenimiento.

## Related
*   ADR-005 (#36) — Doctrina de Ingesta Unificada.
*   Issue 4.3 (#29) — Implementación de Handlers Cognitivos (`vectorize_event` como bala trazadora del sistema de contratos).
*   Issue 4.4 (#30) — Resiliencia, Retries y DLQ (taxonomía de fallos fijada aquí).
*   BUG-002 (#35) — absorbido/superado por la validación síncrona en la frontera.

---

# ISSUES

### [ADR-006-001] Registro de Contratos de Tarea (Task Contract Registry) — #43
Registro central de contratos como única fuente de verdad: `validate`/`schema`/`list()` servidos del mismo objeto; contratos formales para `vectorize_event` y `consolidate_memory`; sin contrato no hay despacho (error explícito). Fixture de contrato para `dummy_test_task` en la suite del tracer del ADR-004.

### [ADR-006-002] Exposición de Contratos como MCP Resources (Descubrimiento antes que Aplicación) — #42
Recurso catálogo `coreai://contracts/tasks` + template `coreai://contracts/tasks/{task_type}`, servido desde el registro.

### [ADR-006-003] Validación Síncrona de Contrato en el Dispatch (Frontera de la Tool) — #44
`coreai_dispatch_task` valida forma en la tool call; violación → `isError=True` con contrato inline + URI del resource.

### [ADR-006-004] Bala Trazadora del Sistema de Contratos: `vectorize_event` (desbloqueo del Issue 4.3) — #41
El handler de `vectorize_event` consumiendo su contrato registrado; retira la extracción resiliente con claves candidatas.
