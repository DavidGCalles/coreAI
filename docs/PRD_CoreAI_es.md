# Product Requirements Document (PRD): [coreAI] - Nivel Arquitectura Base

> **Estado de revisión (nov 2026):** este PRD refleja la doctrina vigente del repo (ADR-001 a ADR-012). Las divergencias con la especificación fundacional (sept 2026), y sus razones, quedan documentadas en la sección 6; la fuente viva del estado del proyecto es el README junto con `docs/adr/`.

## 1. Naturaleza del Sistema (El Motor Core)
[coreAI] es un backend transaccional de cognición asíncrona y gestión de estado. Actúa como el sistema nervioso central (headless) para cualquier agente o cliente externo. Su función no es "hacer cosas", sino mantener la coherencia de la realidad (datos), procesar contexto en la sombra y exponer una interfaz estandarizada para que otros operen. Es un **sustrato, no un cerebro**: el gateway enruta, no piensa; la orquestación interactiva vive en el cliente de borde; [coreAI] garantiza persistencia, memoria unificada y procesamiento de fondo.

## 2. Subsistema de Memoria (Grafo Híbrido Relacional-Vectorial)
La memoria no es un simple almacén, es un motor de resolución de entidades y contexto temporal.

*   **Motor Relacional (PostgreSQL):** Custodio absoluto de la verdad. Define el esquema duro:
    *   *Registro de Entidades:* declarativo y gobernado en el despliegue (YAML montado + bootstrap idempotente, ADR-012-001): nodos del sistema (personas, proyectos, conceptos, dispositivos de infraestructura) cuya vida pasa por revisión (config, commit, PR). Nada se auto-vivifica más allá de `system_default`.
    *   *Historial Episódico:* log inmutable de todas las transacciones, interacciones y telemetría, con el actor y la entidad de cada tarea en primer nivel relacional (`tasks.actor`, `tasks.entity_id`, ADR-012-003/004) y titularidad por registro en la escritura (ADR-012-004).
    *   *Control de Acceso:* diferido por diseño — el ADR-012 sienta identidad y visibilidad por registro (filtro `owner` en el recall, ADR-012-005); el RBAC completo vivirá en un ADR posterior que consume esa identidad (ver sección 6).
*   **Motor Semántico (Qdrant):** memoria vectorial derivada, en estricta subordinación a la relacional: cada vector es un apéndice de un registro determinista; nunca texto sin respaldo relacional. La visibilidad respeta la titularidad por registro.
*   **Esquema aditivo con bootstrap idempotente (ADR-011):** los modelos ORM son la única fuente de verdad del esquema; el arranque garantiza tablas, columnas y enums que falten; evolución solo aditiva, sin migraciones (ver sección 6).
*   **Decaimiento y Compresión:** la compresión de eventos antiguos en recuerdos consolidados la ejecuta el córtex asíncrono (contrato `ConsolidateMemory`), con trazabilidad en el registro relacional; la selección semántica con límite de cantidad en tokens sigue abierta (BUG-006, #69).

## 3. Frontera de Protocolo (MCP)
[coreAI] expone una superficie fija, estable y aditiva bajo el estándar Model Context Protocol (MCP).

*   **Tools con contrato formal (ADR-006):** cada task type despachable lleva contrato registrado, validado síncronamente en la frontera y publicado como resource: sin contrato no hay despacho. Hoy: ingesta (`coreai_dispatch_task`), estado (`coreai_get_task`), DLQ (`coreai_list_tasks`, `coreai_retry_failed_task`) y recall read-only (`coreai_search_context`, `coreai_search_image`).
*   **Orquestación de Entradas/Salidas:** los clientes — IDEs, agentes, clientes de borde — solo ven la frontera MCP; jamás interactúan con los servicios core (PostgreSQL, Qdrant, Infinity, worker).
*   **Aislamiento:** si un handler falla, la tarea transita su ciclo de reintentos con backoff y muere en la DLQ con auditoría íntegra; el resto del sistema sigue.
*   El registro dinámico de capacidades en caliente de la especificación original quedó descartado (ver sección 6).

## 4. Córtex Asíncrono (Event-Driven Background Processing)
El sistema no puede depender de que un cliente haga un *request* para pensar; pero tampoco de un broker externo.

*   **Pipelines de Ingesta:** toda escritura mutante entra como tarea por la event lane (`coreai_dispatch_task`, ADR-005), con contrato por tipo (ADR-006) e identidad resuelta en la frontera (ADR-012).
*   **Cola nativa sobre PostgreSQL:** `SELECT ... FOR UPDATE SKIP LOCKED` sobre la tabla `tasks` — consistencia fuerte (ACID) y resiliencia a caídas de contenedores sin broker (ver sección 6).
*   **Workers Desacoplados:** consumidores asíncronos ejecutan los handlers registrados por tipo de tarea: vectorización de eventos y de imagen (ADR-007), corrección de metadata sin re-embedding (ADR-009), consolidación de memoria.
*   **Resiliencia:** reintentos con backoff exponencial y jitter (`TASK_MAX_RETRIES`, `TASK_BACKOFF_*`), DLQ lógica (estado FAILED) con `error_history` intacto y reinyección manual por la frontera (4.4, #105).
*   **Rutinas de Mantenimiento:** la consolidación nocturna vive en la event lane (contrato `ConsolidateMemory`), programada por los schedulers del despliegue.

## 5. Enrutamiento de Inferencia Soberano (LLM Proxy)
[coreAI] es agnóstico al motor cognitivo, pero prioriza la soberanía de los datos.

*   **Proxy Transparente (LiteLLM):** todo tráfico de inferencia pasa por un embudo controlado, con telemetría transaccional del consumo de tokens en `llm_audit`.
*   **Enrutado determinista local-first:** las tareas internas del córtex se enrutan al plano local: Infinity sirve los embeddings (e5, NLI, jina-clip-v2) y el chat apunta al modelo local del host (LM Studio) a través del gateway.
*   **Sin fallbacks externos (ADR-002):** si el hardware local cae, el error es explícito y las colas se detienen; ningún mecanismo deriva peticiones a APIs externas de pago (ver sección 6).

## 6. Divergencias con la especificación original y sus razones

La versión fundacional de este PRD (sept 2026) especificaba el sistema antes de las decisiones estructurales. Registro de lo que cambió, por qué, y por qué la parte original no se ejecutó como se esperaba:

1. **Broker de mensajería → cola nativa sobre PostgreSQL (Epic 4).** La especificación pedía Event Bus / Message Queue (Redis/RabbitMQ, Celery/RQ). No se implementó: para un sustrato local-first, la consistencia fuerte (ACID) y la resiliencia ante caídas de contenedores se garantizan mejor con la propia tabla `tasks` y bloqueos transaccionales por fila (`FOR UPDATE SKIP LOCKED`); un broker añade una pieza que operar y estados distribuidos que sincronizar, sin aportar garantías nuevas (ver el contexto del epic en `docs/issues/epic-04.md`).
2. **Migraciones Alembic → bootstrap idempotente (ADR-011).** El sistema de migraciones quedó fuera del repo: los modelos ORM son la única fuente de verdad y el arranque garantiza lo que falte. Razón: el arranque autónomo (`docker compose down -v` → `up` ya procesa un dispatch sin pasos manuales) vale más que el historial de migraciones en un despliegue on-premise; el esquema evoluciona solo de forma aditiva.
3. **Fallback externo automático → sin fallbacks (ADR-002).** La especificación pedía derivar a APIs externas de pago si la inferencia local fallaba. Se eliminó: con workers drenando colas en segundo plano, un colapso del hardware local habría derivado miles de peticiones silenciosas a proveedores externos (fuga financiera) y enmascarado el fallo de infraestructura. Si el metal cae, el error es explícito y obliga a intervenir (post-mortem del Epic 3, `docs/issues/epic-03.md`).
4. **Registry dinámico de capacidades → superficie fija con contratos (ADR-006).** La especificación pedía un endpoint donde servicios externos registraran manifiestos en caliente. Se descartó: la superficie de tools es un contrato estable que los inquilinos consumen; el registro en caliente abría superficie sin consumidor real y sacaba la evolución del circuito de revisión (ADR + PR).
5. **RBAC a nivel de BD → identidad primero, permisos diferidos (ADR-012).** La especificación asumía autenticación y permisos en la capa de datos desde el inicio. Se diferó: un modelo de permisos sin identidad resuelta es un filtro sin sujeto. El ADR-012 aterrizó entidades declarativas, resolución en la frontera con fallo determinista, actor de trazabilidad y titularidad por registro; el RBAC completo consumirá esa identidad en su propio ADR.
6. **Herramienta única `memory_tool` → superficie semántica de 6 tools.** La herramienta única CRUD + similitud mezclaba semánticas distintas. La superficie real separa ingesta (event lane asíncrona), estado y DLQ (read-only + reinyección), y recall (texto/imagen, read-only): cada apertura con contrato explícito y clase de fallo propia (ADR-005/006).
