# MoSCoW: [coreAI] - Versión Fundacional (V1.0) · Revisión V1.1 (nov 2026)

> Revisión V1.1: los ítems se ajustan a la doctrina vigente del repo. Cada cambio frente a la V1.0 fundacional (sept 2026) se anota en cursiva con su motivo; el detalle completo de razones vive en la sección 6 del PRD y en los ADRs.

## MUST HAVE
*   **Infraestructura Base:** Orquestación con `docker-compose` para aislar servicios (frontera MCP, DBs, proxy, workers, observabilidad). *(V1.1: sin broker de mensajería — ver Córtex Asíncrono.)*
*   **Motor Relacional Base:** PostgreSQL + SQLAlchemy con el esquema de Entidades y Eventos (UUIDs primarios) garantizado al arranque por bootstrap idempotente (ADR-011). *(V1.1: sin Alembic — el arranque autónomo vale más que el historial de migraciones en un despliegue local-first.)*
*   **Motor Semántico Base:** Qdrant desplegado y operativo, con respaldo relacional estricto (vectores derivados; nunca texto huérfano).
*   **Gateway MCP:** Servidor MCP en STDIO + streamable HTTP (ADR-008), con superficie de 6 tools bajo contrato formal (ADR-006): ingesta, estado, DLQ y recall de texto/imagen. *(V1.1: la herramienta única `memory_tool` quedó sustituida por esta superficie semántica — cada apertura con contrato y clase de fallo propia.)*
*   **Córtex Asíncrono (Workers):** cola nativa sobre PostgreSQL (`SELECT ... FOR UPDATE SKIP LOCKED`) consumiendo eventos, con reintentos, backoff y DLQ. *(V1.1: sin Celery/RQ ni Redis — la consistencia ACID y la resiliencia ante caídas se garantizan con la propia tabla `tasks`.)*
*   **Enrutamiento Básico:** LiteLLM determinista hacia el plano local (Infinity para embeddings, modelo local para chat), sin fallbacks externos (ADR-002). *(V1.1: el fallback automático a APIs de pago era una fuga financiera silenciosa y enmascaraba fallos de infraestructura.)*

## SHOULD HAVE
*   **Rutina de Consolidación (Cron):** contrato `ConsolidateMemory` + handler en main; la selección semántica con límite de cantidad en tokens sigue abierta (BUG-006, #69).
*   **Telemetría de Costes:** registro transaccional del consumo de tokens en `llm_audit` — implementado (Epic 3).

## COULD HAVE
*   **Registro Dinámico de Herramientas:** descartado por ahora — la superficie estable con contratos (ADR-006) es parte del diseño; se reabrirá por un caso de uso real, no por hipótesis.
*   **Control de Acceso (RBAC):** diferido — consumirá la identidad resuelta del ADR-012 (entidades declarativas, actor, titularidad por registro) en su propio ADR.
*   **Decaimiento Avanzado de Memoria:** pendiente — algoritmos para archivar lógicamente recuerdos viejos según su curva de retención.

## WON'T HAVE
*   **Interfaces (UI/UX), clientes de mensajería o frontends de cualquier tipo.**
*   **Lógicas de orquestación de Agentes o modelos de "Swarm".**
*   **Memoria efímera de sesión orientada a flujos de conversación.** *(V1.1: la exploración de sesiones gestionables vive abierta en #85.)*
*   **Herramientas MCP de prueba o utilidades genéricas ajenas a la persistencia.**
