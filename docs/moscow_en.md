# MoSCoW: [coreAI] - Foundational Version (V1.0) · Revision V1.1 (Nov 2026)

> Revision V1.1: items adjusted to the repo's current doctrine. Each change from the foundational V1.0 (Sept 2026) is noted in italics with its reason; the full rationale lives in section 6 of the PRD and in the ADRs.

## MUST HAVE
*   **Base Infrastructure:** `docker-compose` orchestration to isolate services (MCP frontier, DBs, proxy, workers, observability). *(V1.1: no message broker — see Async Cortex.)*
*   **Base Relational Engine:** PostgreSQL + SQLAlchemy with the Entities and Events schema (primary UUIDs) guaranteed at startup by idempotent bootstrap (ADR-011). *(V1.1: no Alembic — autonomous startup outweighs migration history in a local-first deployment.)*
*   **Base Semantic Engine:** Qdrant deployed and operational, with strict relational backing (derived vectors; never orphan text).
*   **MCP Gateway:** MCP server on STDIO + streamable HTTP (ADR-008), with a 6-tool surface under formal contract (ADR-006): ingestion, status, DLQ, and text/image recall. *(V1.1: the single `memory_tool` was replaced by this semantic surface — each opening with its own contract and failure class.)*
*   **Async Cortex (Workers):** native queue on PostgreSQL (`SELECT ... FOR UPDATE SKIP LOCKED`) consuming events, with retries, backoff, and DLQ. *(V1.1: no Celery/RQ or Redis — ACID consistency and crash resilience are guaranteed by the `tasks` table itself.)*
*   **Basic Routing:** deterministic LiteLLM toward the local plane (Infinity for embeddings, local model for chat), no external fallbacks (ADR-002). *(V1.1: automatic fallback to paid APIs was a silent financial leak and masked infrastructure failures.)*

## SHOULD HAVE
*   **Consolidation Routine (Cron):** `ConsolidateMemory` contract + handler in main; semantic selection with a token budget remains open (BUG-006, #69).
*   **Cost Telemetry:** transactional record of token usage in `llm_audit` — implemented (Epic 3).

## COULD HAVE
*   **Dynamic Tool Registry:** discarded for now — the stable contract surface (ADR-006) is part of the design; it will be reopened by a real use case, not by hypothesis.
*   **Access Control (RBAC):** deferred — it will consume the identity resolved by ADR-012 (declarative entities, actor, per-record ownership) in its own ADR.
*   **Advanced Memory Decay:** pending — algorithms to logically archive old memories by their retention curve.

## WON'T HAVE
*   **Interfaces (UI/UX), messaging clients, or frontends of any kind.**
*   **Agent orchestration logics or "Swarm" models.**
*   **Ephemeral session memory oriented to conversation flows.** *(V1.1: the exploration of manageable sessions remains open in #85.)*
*   **MCP test tools or generic utilities unrelated to persistence.**
