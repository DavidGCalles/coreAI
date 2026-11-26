# ADR-012: Identidad y visibilidad en la frontera — el sustrato sabe quién despacha

## Status
Proposed (25 nov 2026). Familia arrancada el 25 nov 2026: [ADR-012-001] #112, [ADR-012-002] #113, [ADR-012-003] #114, [ADR-012-004] #115 y [ADR-012-005] #116 como sub-issues nativos de esta issue; el doc aterriza al repo con el arranque, en PR propia (regla de la casa: el doc del ADR aterriza con el arranque de la familia, no al final). Refinado D1–D4 con David el 25 nov: sección «Refinado», abajo.

## Context and Problem Statement

Estado real del repo (main @ 9038095, 25 nov 2026): coreAI resuelve la identidad de cada despacho de forma implícita. La frontera (`coreai_dispatch_task`, ADR-006) acepta un `project` (FR-3, #51), pero `TaskDispatchResponse` nunca devuelve la `entity_id` resuelta: cuando el despachante no declara una identidad conocida, la entidad se auto-vivifica en `system_default` sin rastro en la respuesta. El actor que despacha no viaja en el contexto de la tarea, y la memoria unificada no lleva visibilidad por registro: todo punto almacenado es visible para cualquiera que pregunte por él (recall read-only `coreai_search_context`).

La multitenancy fue diferida el 26 oct (PR #70): `MemorySearchFilters.tenant_id` quedó en `None` y su restauración es un cambio local en `_build_filter`. Es decir: el sustrato ya tiene el hueco del inquilino marcado en el código, pero hoy nadie lo rellena.

Mientras tanto, cada capability que aterriza añade registros sin identidad: la corrección de memoria (ADR-009) enmienda puntos que cualquiera podría haber escrito, y la DLQ del 4.4 (#30) dejará tareas fallidas sin dueño. Y la siguiente propuesta estructural, los catálogos abiertos, tiene el inquilino como eje (taxonomía por inquilino): sin identidad resuelta en la frontera, esa taxonomía se diseñaría contra `system_default` y obligaría a re-tocar el mismo contrato después.

**Pregunta:** ¿cómo incorpora coreAI identidad y visibilidad en la frontera de despacho y en la memoria unificada, de forma aditiva con la superficie de contratos actual (ADR-006/009) y con el esquema aditivo (ADR-011), sin cerrar el modelo de permisos que venga después?

## Decision Drivers

*   El sustrato es responsable del perímetro, no del contenido — identidad y visibilidad son decisión de la frontera, no de los clientes edge (substrate, not a brain).
*   Superficie estable, evolución aditiva — los contratos viven expuestos como MCP resources; un campo nuevo es aditivo, un cambio de semántica rompe inquilinos.
*   Coste de retrofit creciente — cada capability mergeada sin identidad amplía la superficie que la visibilidad deberá cubrir después; el orden de este ADR es en sí mismo la decisión más barata.
*   Arranque autónomo — nada que exija provisión previa de usuarios al desplegar; el arranque limpio y por `include:` sigue garantizándose solo (ADR-011).
*   Eje de las extensiones futuras — los catálogos abiertos y cualquier RBAC posterior consumen la identidad resuelta; este ADR es su prerrequisito.

## Considered Options

*   Opción A — Provisión declarativa de entidades (config + bootstrap) + resolución en la frontera con error no recuperable para identidades no registradas + actor en el contexto + visibilidad por registro (elegida).
*   Opción B — Diferir identidad y abrir primero los catálogos: la taxonomía se diseña contra `system_default` y la identidad se retrofit más adelante.
*   Opción C — Restaurar ya la multitenancy completa (filtro `tenant_id` duro en el recall, PR #70) antes de resolver identidad.
*   Opción D — Herramienta síncrona de registro de entidades (`coreai_register_entity`): alta en runtime, self-service para clientes con entidades dinámicas.

## Decision Outcome

**La identidad se declara en el despacho, se resuelve en la frontera y se gobierna en el despliegue: el sustrato responde quién despachó, con qué entidad quedó registrada la tarea y qué registros puede ver cada quien.** Concretamente:

- **Entidades declarativas, gobernadas en el despliegue.** Las identidades del inquilino viven en la config del stack y el bootstrap idempotente las garantiza al arranque (patrón `ensure_*` del ADR-011; `system_default` implícito). La vida de una identidad pasa por revisión humana: config, commit y PR. Nada se auto-vivifica más allá de `system_default`.
- **Resolución en la frontera, tres casos.** Declarada y registrada → `TaskDispatchResponse` devuelve la `entity_id` resuelta, que viaja al contexto para handlers y trazabilidad. Declarada y no registrada → error no recuperable con catálogo inline (veneno determinista, estilo ADR-006/009: reintentar el mismo payload no sirve). Ausente → `system_default`, como hoy, pero visible en la respuesta y en la titularidad de todo lo escrito: el arranque autónomo sigue sin exigir provisión previa y la resolución deja de ser silenciosa.
- **Actor en el contexto de la tarea.** Free-string de trazabilidad dentro del namespace de la entidad, aditivo y opcional en el contrato (sin actor declarado, el valor es el de la entidad resuelta). El veneno aplica a la entidad, no al actor: la autenticación del humano vive en el edge, nunca en el core.
- **Visibilidad por registro en la memoria unificada.** Cada punto almacenado lleva metadatos de titularidad; el recall los respeta como filtro, restaurando el hueco ya marcado en `_build_filter` (PR #70).
- **Lo que este ADR NO decide:** la tool de registro de entidades en runtime (vía natural si un cliente real necesita entidades dinámicas; se justificará por un diff entre implementaciones, no por hipótesis); el modelo de permisos (roles, capacidades, políticas: el RBAC completo vive en un ADR posterior que consume esta identidad); el aislamiento duro multitenant (diferido: este ADR sienta visibilidad por registro, no aislamiento); y la implementación concreta (familia [ADR-012-NNN]).

### Positive Consequences
*   **Los catálogos abiertos quedan desbloqueados**: la taxonomía por inquilino se diseñará contra identidad resuelta, en una sola pasada sobre la frontera.
*   **El retrofit se detiene**: a partir de aquí, cada capability nueva aterriza con titularidad, no con deuda de visibilidad.
*   **Gobernanza de identidad auditable**: una identidad solo existe tras revisión de PR, con historia en git; el veneno mata en la frontera los typos (``david1`` nunca llega a la tabla).
*   **Trazabilidad real**: actor y entidad en cada tarea; el amend (ADR-009) y la DLQ del 4.4 ganan dueño verificable.

### Negative Consequences
*   **La superficie de contratos crece** (campos nuevos en respuesta y contexto): coste de mantenimiento asumido, aditivo por diseño (ADR-006).
*   **El modelo de permisos queda abierto**: visibilidad por registro sin política de acceso es un filtro, no una garantía de seguridad; se declara explícitamente incompleto hasta su ADR.
*   **Los registros históricos quedan sin titularidad**: los puntos ya almacenados no tienen actor; su visibilidad se resuelve por regla de cierre decidida en la familia.
*   **El alta de una entidad exige tocar el despliegue** (config + arranque): coste deliberado; la vida de una identidad no es self-service en runtime hasta que un diff real justifique lo contrario.

## Pros and Cons of the Options

### Opción A — Declarativa + veneno en la frontera (elegida)
*   Good, porque desbloquea catálogos abiertos y RBAC sin re-tocar la frontera después.
*   Good, porque es aditiva con los contratos (ADR-006) y el esquema (ADR-011), y preserva el arranque autónomo.
*   Good, porque la gobernanza de identidad queda en revisión de PR, no en una llamada runtime.
*   Bad, porque crece el contrato de despacho antes de tener su consumidor pleno (el RBAC posterior).
*   Bad, porque el alta de entidades pasa por el despliegue: fricción asumida a cambio de deliberación.

### Opción B — Catálogos primero
*   Good, porque es la capability menor y la de resultado más visible a corto plazo.
*   Bad, porque diseña la taxonomía contra `system_default` y condena a una segunda pasada sobre la misma frontera.
*   Bad, porque cada capability que entre mientras tanto aterriza sin titularidad y agranda el retrofit.

### Opción C — Multitenancy completa primero
*   Good, porque daría aislamiento duro desde el primer día.
*   Bad, porque restaura un mecanismo diferido (PR #70) antes de resolver la identidad: un inquilino sin identidad resuelta es un identificador sin sujeto.
*   Bad, porque excede el alcance que los clientes del sustrato ejercen hoy y abre superficie sin consumidor.

### Opción D — Tool síncrona de registro (`coreai_register_entity`)
*   Good, porque da alta inmediata y cubriría entidades dinámicas de clientes en runtime.
*   Bad, porque sería la primera apertura síncrona de escritura en la frontera (hoy la única síncrona es read-only, ADR-005).
*   Bad, porque el registro sin fricción de revisión degrada el veneno: `david1` se registra tan fácil como se vivificaría.
*   Neutral, porque no se descarta para siempre: la abrirá un diff entre dos clientes reales que la necesiten, en su propio ADR.

## Refinado (25-26 nov 2026, chat con David)

- **D1 — Regla de cierre de históricos:** backfill idempotente a `system_default` en el propio bootstrap (se ejecuta en [ADR-012-005] #116); nadie pierde acceso al subir la familia y la regla queda auditable en git.
- **D2 — Declaración de entidades:** fichero **YAML montado** en el despliegue ([ADR-012-001] #112); la env var queda descartada: cualquier aplicación consumidora podría leerse el env de entidades según el caso de uso.
- **D3 — owner ≠ visibility:** la titularidad (`owner`, metadata nueva) y el flag `visibility` preexistente son dos conceptos separados ([ADR-012-004] #115); base del RBAC posterior y del cruce de conocimientos entre propietarios, sin fusión ni renombrado.
- **D4 — Alcance del veneno:** la entidad declarada y no registrada es fallo determinista en TODAS las task types ([ADR-012-002] #113); la frontera es una sola.
- **D5 — Resolución del slug a la escritura:** sin columna slug duplicada en `tasks` (doble verdad de la misma entidad); el `owner` del punto Qdrant es el slug de la entidad titular resuelto a la escritura con un join contra `entities` — el mismo helper que reutiliza el backfill de #116. Fallback de legado: task pre-familia aún PENDING sin columna estampada → titularidad por sesión, documentada (idéntico al actor `None` de `error_history`).
- **D6 — Identidad en primer nivel relacional (26 nov, David):** actor y titularidad viven en COLUMNAS (`tasks.actor`, `tasks.entity_id`), JAMÁS enterrados en el JSONB del contexto ([ADR-012-003] #114 re-homeado, [ADR-012-004] #115): un `actor`/`owner` en el `task_payload` rebota por `extra=forbid` — nada de identidad viaja por contrato. `tenant_id` preexistente de los puntos queda intacto (D3 en ambas direcciones: aislamiento de cómputo ≠ titularidad legible); el owner del punto es el slug, el amend NO reescribe titularidad — estampa `amend_entity`/`amend_actor` idempotentes como traza del corrector; el residuo JSONB de la ventana pre-re-home se respeta como historia (append-only).
- **Orden de ejecución:** esta familia precede a la del ADR-010 (#93): cada capability que aterrice sin identidad amplía el retro-fit que la visibilidad deberá cubrir después.

## Related

*   Familia: [ADR-012-001] #112, [ADR-012-002] #113, [ADR-012-003] #114, [ADR-012-004] #115 y [ADR-012-005] #116 — desglose de implementación, sub-issues nativos de esta issue.
*   ADR-006 (#40) — Contratos de tarea MCP: la identidad se resuelve y valida en la misma frontera que ya valida la forma.
*   ADR-009 (#88) — Corrección de memoria: el amend gana actor y titularidad verificable.
*   ADR-011 (#99) — Esquema aditivo con bootstrap idempotente: las columnas de actor/titularidad aterrizan al arranque, sin pasos manuales.
*   PR #70 — Multitenancy diferida: `MemorySearchFilters.tenant_id` → `None`; la restauración del filtro es cambio local en `_build_filter`.
*   Propuesta de catálogos abiertos (sin issue aún) — taxonomía por inquilino: consumidora directa de la identidad resuelta aquí.

---

# ISSUES

### [ADR-012-001] Provisión declarativa de entidades (YAML montado + bootstrap idempotente) — #112
Pendiente (25 nov). Oráculo de registro para la resolución de la frontera.

### [ADR-012-002] Resolución de identidad en la frontera de despacho — #113
Pendiente (25 nov). Los tres casos de resolución, fin de la auto-vivificación, `tasks.entity_id` aditiva (dueño para la DLQ de la 4.4).

### [ADR-012-003] Actor en el contexto de la tarea — #114
Pendiente (25 nov). Free-string de trazabilidad con default = entidad resuelta; enriquece logs del worker y `error_history`.

### [ADR-012-004] Titularidad por registro en la escritura (owner ≠ visibility) — #115
Pendiente (25 nov). `owner` estampado desde el contexto en Qdrant y Postgres; separación explícita de `owner` y `visibility` (D3).

### [ADR-012-005] Visibilidad por registro en el recall + regla de cierre de históricos — #116
Pendiente (25 nov). Filtro opcional de propietario en `coreai_search_context`, restauración del hueco de la PR #70 en `_build_filter` y backfill idempotente de históricos a `system_default` (D1).
