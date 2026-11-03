# ADR-009: Superficie de Corrección de la Memoria — El Ciclo de Vida de lo Ya Almacenado es del Substrato

## Status
Accepted (3 nov 2026) — [ADR-009-001] #89 implementado (PR #90): contrato `amend_memory_point`, handler dual-store y E2E de punta a punta en verde. Las dos decisiones abiertas del ADR quedan cerradas con evidencia en la Post-implementación. Proposed 1 nov 2026.

## Context and Problem Statement

coreAI escribe memoria por una sola puerta: toda mutación de estado entra como evento despachable y la aplica un handler (ADR-005). La lectura tiene su apertura síncrona read-only (recall). El resultado es que la memoria es append-only en la práctica: los puntos solo se acumulan.

Si un punto aterriza mal registrado — una transcripción errónea, un filtro de identidad mal resuelto en la ingesta, un valor de `project` equivocado — no existe camino de corrección. La frontera expone escritura (dispatch de eventos) y lectura (recall); ninguna superficie muta lo ya almacenado. Y el punto vive en dos almacenes: fuente de verdad relacional en PostgreSQL y vector + payload en Qdrant. Un cliente no puede corregir con consistencia desde fuera; la mutación coordinada de ambos almacenes es territorio del substrato.

La pieza que faltaba ya existe en el repo: los hits del recall exponen `point_id`, y Qdrant sirve `set_payload` por point ID — la corrección es viable con precisión, sin tocar el vector.

**Pregunta:** ¿cómo expone coreAI la mutación de puntos ya almacenados, bajo qué doctrina, y con qué alcance en su primera iteración?

## Decision Drivers

*   Doctrina de escritura intacta — toda mutación de memoria pasa por eventos y handlers; ninguna tool síncrona escribe en la memory layer.
*   Consistencia dual-store — el punto vive en PostgreSQL y Qdrant; solo el substrato puede mutar ambos con garantías.
*   Genérico y licenciable — cero semántica de dominio: el core posee el mecanismo, el inquilino posee los valores (misma forma que el campo `project`, FR-3). Pasa el test del banco.
*   Precisión sin coste — el `point_id` ya viaja en el recall; `set_payload` corrige metadata sin re-embedding.
*   Trazabilidad de ciclo de vida — toda corrección debe dejar traza audible, como cualquier otra escritura.

## Considered Options

*   Evento del ciclo de vida — task type nuevo despachable, aplicado por su handler.
*   Tool síncrona de mutación — tool MCP nueva que corrige puntos en línea.
*   Re-ingesta — registrar el dato de nuevo bajo el valor correcto y convivir con el punto mal registrado.

## Decision Outcome

**La corrección de puntos ya almacenados es un evento del ciclo de vida, no una tool síncrona** — un task type nuevo despachable, con contrato en el registry (ADR-006), aplicado por su handler: los handlers siguen siendo los únicos writers de la memory layer (ADR-005).

- **Sin tool nueva en la frontera.** El identificador necesario ya viaja en la lectura: los hits del recall exponen `point_id`. El inquilino despacha un evento que referencia el punto; el status se consulta por `coreai_get_task`, write-then-read intacto — jamás inferido del search.
- **Alcance v1: corrección de metadata, no de contenido.** El evento corrige campos de payload (`project` en primera instancia) con `set_payload` por point ID en Qdrant más la actualización simétrica en PostgreSQL. El contenido vectorizado no cambia: el vector existente sigue válido, no se re-embedea.
- **Reescribir contenido queda fuera por diseño.** Quien necesita otro contenido re-ingesta: ese camino ya existe. La corrección re-orienta lo almacenado, no lo reescribe.
- **Idempotencia por diseño.** Re-dispatch de la misma corrección es seguro: el payload converge al valor objetivo.
- **Punto inexistente = fallo explícito.** Si el point_id referenciado ya no existe (p. ej. absorbido por consolidación), el evento falla de forma visible — jamás silencio.
- La corrección aplica por igual a texto e imagen (`point_id` transversal a las colecciones) y respeta el diferimiento de la identidad multi-tenant.

Lo que este ADR NO decide:

- El catálogo de campos corregibles más allá de `project` — añadir campo al contrato es cambio local posterior.
- Borrado o retractación de puntos — la corrección re-orienta, no destruye.

Decisiones no cerradas todavía (se cierran con evidencia en la implementación de [ADR-009-001] y se registran en Post-implementación):

- **Estrategia transaccional dual-store** — orden PostgreSQL/Qdrant y compensación ante fallo a mitad de la corrección.
- **Clase exacta del fallo por punto inexistente** — que es explícito y nunca silencio ya está decidido; si es veneno no-reintentable o no-op idempotente no.

### Positive Consequences
*   **Superficie de actuación real sobre memoria acumulada**, sin romper la doctrina de writers: mutación por evento, handler único writer.
*   **Auditabilidad de ciclo de vida.** Toda corrección es un evento con su task row: la memoria deja de ser una acumulación opaca.
*   **Genérico y licenciable.** Mecanismo del core, valores del inquilino.
*   **Barato.** `set_payload` sin re-embedding cuando el contenido no cambia: corregir metadata no cuesta cómputo de modelos.

### Negative Consequences
*   **Un task type nuevo es superficie de contrato que crece**: catálogo, validación en frontera y taxonomía de fallo en el router.
*   **Dos almacenes mutados por un handler.** La simetría exige estrategia transaccional explícita — decisión no cerrada de este ADR.
*   **Corrección eventual, no síncrona.** Consistente con todas las escrituras, pero sin confirmación inmediata para el inquilino.

## Pros and Cons of the Options

### Evento del ciclo de vida

*   Good, porque mantiene intacta la doctrina de escritura (handlers únicos writers) y hereda gratis la trazabilidad del task row.
*   Good, porque no añade superficie a la frontera: el `point_id` ya viaja en el recall y `coreai_get_task` ya sirve el status.
*   Bad, porque la corrección es eventual — no hay confirmación síncrona.

### Tool síncrona de mutación

*   Good, porque daría feedback inmediato al inquilino.
*   Bad, porque rompe la doctrina de ingesta unificada: una segunda superficie de escritura en la memory layer fuera del lane asíncrono.
*   Bad, porque el cliente no puede garantizar la consistencia dual-store — la mutación coordinada es territorio del substrato, no de la frontera.

### Re-ingesta

*   Good, porque cero superficie nueva: es el camino que ya existe.
*   Bad, porque el punto mal registrado queda para siempre: contamina los filtros por `project` y el recall devuelve resultados erróneos de por vida. La memoria acumulada no admite higiene.
*   Bad, porque no escala como política: cada error de registro se paga con un huérfano permanente.

## Related

*   ADR-005 (#36) — Doctrina de Ingesta Unificada.
*   ADR-006 (#40) — Contratos de Tarea MCP.
*   ADR-007 (#48) — Memoria Multimodal.
*   BUG-006 (#69) — Consolidación por temática.

## Post-implementación (3 nov 2026)

- **Contrato aterrizado:** task type `amend_memory_point` con `AmendMemoryPointPayload` (`point_id` + `project`, `extra=forbid`) registrado en el registry — al registrarse es despachable por la frontera y se publica como resource (ADR-006) sin tocar la superficie MCP (4 tools intactas) ni `route_task`.
- **Decisión cerrada — estrategia transaccional dual-store:** Qdrant primero (`set_payload` por point ID, `wait=True`), Postgres después, **cero compensación activa**. Ambas escrituras son sets idempotentes: un fallo a mitad deja FAILED honesto con `error_trace` y el re-dispatch converge al valor objetivo. La asimetría del esquema dicta la simetría relacional: en texto, `project` solo vive en el payload de Qdrant (`Event` no lo lleva) → la traza relacional de la corrección es la task row del amend (`amended_collection` + `old_project`, append-only); en imagen, la task original lleva el payload en JSONB (ADR-007-003) → espejo simétrico por UUID (`TaskRepository.set_payload_fields`). La colección se auto-resuelve (`locate_point` sobre las colecciones registradas): la topología es del substrato, no del contrato.
- **Decisión cerrada — fallo por punto inexistente:** `MemoryPointNotFoundError`, veneno determinista no-reintentable — FAILED explícito con `error_trace`, jamás un no-op COMPLETED. Un no-op escondería point_ids mal copiados o puntos absorbidos por consolidación detrás de un éxito aparente.
- **Verificación:** suite del contrato y del handler (mock-based, sin BD) y E2E de punta a punta en verde en la VM de desarrollo (3 nov): seed mal registrado → dispatch de corrección → poll → el recall por el `project` corregido devuelve EL MISMO punto (el `project` erróneo ya no lo sirve: re-orientado, no duplicado) → punto inexistente = FAILED `MemoryPointNotFoundError`.

---

# ISSUES

### [ADR-009-001] Evento de corrección de memoria — contrato, handler y verificación end-to-end — #89
Completado (3 nov, PR #90). Task type `amend_memory_point` con contrato cerrado (`point_id` + `project`), handler aplicando la mutación dual-store (`set_payload` en Qdrant + simetría relacional) sin re-embedding, suite del contrato y del handler, y E2E de punta a punta en verde (seed mal registrado → dispatch de corrección → poll → recall filtrando por el `project` corregido). Las dos decisiones abiertas del ADR — estrategia transaccional dual-store y clase del fallo por punto inexistente — cerradas con evidencia en el issue y registradas aquí, en la Post-implementación.
