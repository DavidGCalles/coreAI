# Roadmap: [coreAI]

> Estado real a noviembre 2026. Las épicas 1-5 están completadas; el detalle de cada una vive en `docs/issues/` (con sus post-mortems) y las decisiones en `docs/adr/`. Las líneas abiertas están al final.

## Epic 1: [coreAI] Infraestructura Base y Orquestación — ✅ completado
*   **Objetivo original:** Aislamiento y despliegue del entorno base mediante `docker-compose`.
*   **Ejecución real:** compose con redes bridge duales (frontend/backend), logs a Loki y observabilidad completa (Prometheus/Grafana), arranque ordenado por healthchecks (BUG-005), imágenes con nombre explícito (BUG-007), bootstrap de permisos de volúmenes (BUG-008) y esquema garantizado al arranque (BUG-010).

## Epic 2: [coreAI] Capa de Memoria Unificada (Data Layer) — ✅ completado
*   **Objetivo original:** Persistencia determinista y semántica de estado sin fragmentación.
*   **Divergencia:** sin Alembic — el esquema vive en los modelos ORM y el bootstrap idempotente lo garantiza al arranque (ADR-011). Post-mortem en `docs/issues/epic-02.md`.
*   **Post-épica:** identidad y titularidad por registro en la memoria, con visibilidad en el recall (ADR-012).

## Epic 3: [coreAI] Enrutamiento de Inferencia (LLM Proxy) — ✅ completado
*   **Objetivo original:** Abstracción del motor cognitivo con enfoque *local-first*.
*   **Divergencia:** sin fallback externo (ADR-002) — enrutado determinista a Infinity (embeddings) y al modelo local (chat); telemetría transaccional en `llm_audit`. Post-mortem en `docs/issues/epic-03.md`.

## Epic 4: [coreAI] Córtex Asíncrono (Event-Driven Workers) — ✅ completado (epic #26 cerrado)
*   **Objetivo original:** Procesamiento en segundo plano y asimilación autónoma de contexto.
*   **Divergencia:** sin broker — cola nativa sobre PostgreSQL (`FOR UPDATE SKIP LOCKED`), tal como define el propio `docs/issues/epic-04.md`; el planteamiento fundacional (Redis/RabbitMQ, Celery/RQ) quedó descartado por consistencia ACID y resiliencia.
*   **Resultado:** superficie MCP de tareas completa (dispatch, estado, DLQ con reinyección), contratos por task type (ADR-006), memoria multimodal (ADR-007), corrección de memoria (ADR-009).
*   **Resto abierto:** BUG-006 (#69, consolidación por temática).

## Epic 5: [coreAI] Gateway de Comunicación (Servidor MCP) — ✅ completado
*   **Objetivo original:** Exposición estandarizada y hermética del sistema hacia clientes externos.
*   **Divergencia:** la "herramienta central `memory_tool`" del planteamiento original es hoy una superficie de 6 tools con contratos formales (ADR-006), expuestos como resources; serving en STDIO + streamable HTTP (ADR-008).

## Líneas abiertas (nov 2026)
*   **ADR-010:** ingesta de documentos completos y recall tipado (propuesta #93, con addendum #110).
*   **Exploración de gestión de sesiones:** del auto-anclaje por tarea a contexto gestionable del inquilino (#85).
*   **Catálogos abiertos:** taxonomía por inquilino (propuesta sin issue aún) — consumidora directa de la identidad del ADR-012.
*   **RBAC/permisos y tool de registro de entidades en runtime:** explícitamente sin decidir (ADR-012).
