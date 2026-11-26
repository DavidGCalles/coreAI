# Product Requirements Document (PRD): [coreAI] - Base Architecture Level

> **Revision status (Nov 2026):** this PRD reflects the repo's current doctrine (ADR-001 through ADR-012). Divergences from the foundational spec (Sept 2026), and their reasons, are documented in section 6; the live source of project state is the README together with `docs/adr/`.

## 1. Nature of the System (The Core Engine)
[coreAI] is a transactional backend for asynchronous cognition and state management. It acts as the (headless) central nervous system for any agent or external client. Its function is not "to do things", but to keep reality (data) coherent, process context in the background, and expose a standardized interface for others to operate. It is a **substrate, not a brain**: the gateway routes, it does not think; interactive orchestration lives in the edge client; [coreAI] guarantees persistence, unified memory, and background processing.

## 2. Memory Subsystem (Relational-Vector Hybrid Graph)
Memory is not a simple store: it is an engine of entity resolution and temporal context.

*   **Relational Engine (PostgreSQL):** Absolute custodian of the truth. It defines the hard schema:
    *   *Entity Registry:* declarative and deployment-governed (mounted YAML + idempotent bootstrap, ADR-012-001): system nodes (people, projects, concepts, infrastructure devices) whose lifecycle goes through review (config, commit, PR). Nothing auto-vivifies beyond `system_default`.
    *   *Episodic History:* immutable log of all transactions, interactions, and telemetry, with each task's actor and entity at first relational level (`tasks.actor`, `tasks.entity_id`, ADR-012-003/004) and per-record ownership at write time (ADR-012-004).
    *   *Access Control:* deferred by design — ADR-012 establishes identity and per-record visibility (`owner` filter in recall, ADR-012-005); full RBAC will live in a later ADR consuming that identity (see section 6).
*   **Semantic Engine (Qdrant):** derived vector memory, in strict subordination to the relational one: every vector is an appendix of a deterministic record; never text without relational backing. Visibility respects per-record ownership.
*   **Additive schema with idempotent bootstrap (ADR-011):** ORM models are the single source of truth of the schema; startup guarantees any missing tables, columns, and enums; evolution is additive-only, without migrations (see section 6).
*   **Decay and Compression:** compressing old events into consolidated memories is done by the async cortex (`ConsolidateMemory` contract), with full relational traceability; semantic selection with a token budget remains open (BUG-006, #69).

## 3. Protocol Frontier (MCP)
[coreAI] exposes a fixed, stable, additive surface under the Model Context Protocol (MCP) standard.

*   **Tools with formal contracts (ADR-006):** every dispatchable task type has a registered contract, validated synchronously at the frontier and published as a resource: no contract, no dispatch. Today: ingestion (`coreai_dispatch_task`), status (`coreai_get_task`), DLQ (`coreai_list_tasks`, `coreai_retry_failed_task`), and read-only recall (`coreai_search_context`, `coreai_search_image`).
*   **Input/Output Orchestration:** clients — IDEs, agents, edge clients — only see the MCP frontier; they never interact with core services (PostgreSQL, Qdrant, Infinity, worker).
*   **Isolation:** if a handler fails, the task runs its retry cycle with backoff and dies in the DLQ with intact audit; the rest of the system carries on.
*   The original spec's dynamic hot registration of capabilities was discarded (see section 6).

## 4. Async Cortex (Event-Driven Background Processing)
The system cannot depend on a client making a *request* to think; nor on an external broker.

*   **Ingestion Pipelines:** every mutating write enters the event lane as a task (`coreai_dispatch_task`, ADR-005), with a per-type contract (ADR-006) and identity resolved at the frontier (ADR-012).
*   **Native queue on PostgreSQL:** `SELECT ... FOR UPDATE SKIP LOCKED` on the `tasks` table — strong consistency (ACID) and resilience to container crashes without a broker (see section 6).
*   **Decoupled Workers:** async consumers run the handlers registered per task type: event and image vectorization (ADR-007), metadata correction without re-embedding (ADR-009), memory consolidation.
*   **Resilience:** retries with exponential backoff and jitter (`TASK_MAX_RETRIES`, `TASK_BACKOFF_*`), logical DLQ (FAILED state) with intact `error_history`, and manual reinjection through the frontier (4.4, #105).
*   **Maintenance Routines:** nightly consolidation lives in the event lane (`ConsolidateMemory` contract), scheduled by the deployment's schedulers.

## 5. Sovereign Inference Routing (LLM Proxy)
[coreAI] is agnostic to the cognitive engine, but prioritizes data sovereignty.

*   **Transparent Proxy (LiteLLM):** all inference traffic passes through a controlled funnel, with transactional telemetry of token usage in `llm_audit`.
*   **Deterministic local-first routing:** the cortex's internal tasks route to the local plane: Infinity serves embeddings (e5, NLI, jina-clip-v2) and chat targets the host's local model (LM Studio) through the gateway.
*   **No external fallbacks (ADR-002):** if local hardware fails, the error is explicit and queues halt; no mechanism silently diverts requests to paid external APIs (see section 6).

## 6. Divergences from the original spec and their reasons

The foundational version of this PRD (Sept 2026) specified the system before the structural decisions. Record of what changed, why, and why the original part did not play out as expected:

1. **Message broker → native queue on PostgreSQL (Epic 4).** The spec asked for an Event Bus / Message Queue (Redis/RabbitMQ, Celery/RQ). Not implemented: for a local-first substrate, strong consistency (ACID) and resilience to container crashes are better guaranteed by the `tasks` table itself with row-level transactional locks (`FOR UPDATE SKIP LOCKED`); a broker adds a piece to operate and distributed states to synchronize, without new guarantees (see the epic context in `docs/issues/epic-04.md`).
2. **Alembic migrations → idempotent bootstrap (ADR-011).** The migration system left the repo: ORM models are the single source of truth and startup guarantees whatever is missing. Reason: autonomous startup (`docker compose down -v` → `up` already processes a dispatch with no manual steps) outweighs migration history in an on-premise deployment; the schema evolves additively only.
3. **Automatic external fallback → no fallbacks (ADR-002).** The spec asked for diverting to paid external APIs if local inference failed. Removed: with workers draining queues in the background, a local hardware collapse would have silently diverted thousands of requests to external providers (financial leak) and masked the infrastructure failure. If the metal fails, the error is explicit and forces intervention (Epic 3 post-mortem, `docs/issues/epic-03.md`).
4. **Dynamic capability registry → fixed surface with contracts (ADR-006).** The spec asked for an endpoint where external services registered manifests hot. Discarded: the tool surface is a stable contract that tenants consume; hot registration opened surface without a real consumer and took evolution out of the review circuit (ADR + PR).
5. **DB-level RBAC → identity first, permissions deferred (ADR-012).** The spec assumed authentication and permissions at the data layer from day one. Deferred: a permission model without resolved identity is a filter without a subject. ADR-012 landed declarative entities, frontier resolution with deterministic failure, a traceability actor, and per-record ownership; full RBAC will consume that identity in its own ADR.
6. **Single `memory_tool` tool → semantic surface of 6 tools.** The single CRUD + similarity tool mixed distinct semantics. The real surface separates ingestion (async event lane), status and DLQ (read-only + reinjection), and recall (text/image, read-only): each opening with an explicit contract and its own failure class (ADR-005/006).
