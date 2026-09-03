# ADR-001: Implementation of Native Session and Message Entities

## Status
Accepted

## Context
[coreAI] is strictly designed as a headless, asynchronous cognitive backend. The initial data layer specification (Epic 2, Issue 2.1) mandated the creation of `Entity`, `Task`, and `Event` models to handle immutable state and background processing. However, while the immediate Proof of Concept does not target frontend chatbots, any AI-adjacent infrastructure inevitably interfaces with sequential, multi-turn interactions (e.g., chat interfaces, agentic swarms, continuous prompt chains). 

Furthermore, this architecture cannibalizes and generalizes a previous specialized iteration of the system. The `sessions`, `messages`, and `tasks` tables are being dragged from that legacy solution, where they previously formed the load-bearing pillars for multi-turn processing. 

Managing multi-turn interactions strictly through generalized `Event` records introduces critical inefficiencies:
1. It forces complex, expensive chronological aggregations at the database level to reconstruct context windows for the LLM.
2. It delegates session state management entirely to the external clients, violating the principle of [coreAI] as the absolute Source of Truth.

## Decision
We will introduce `Session` and `Message` as first-class entities in the PostgreSQL relational schema, expanding the scope of the foundational ORM deployment, leveraging the proven schemas from the predecessor project.

*   **`Session` Entity:** Will serve as a stateful, chronological container linking a user to a specific continuous interaction thread. It will track execution status (`session_status`).
*   **`Message` Entity:** Will represent individual turns within a session. It includes native fields for `role`, `content`, `visibility`, and `domain` to facilitate direct contextual mapping.
*   **Relational Binding:** Background `Task` entities will be foreign-keyed to a `Session` (rather than floating or attached directly to a user). This ensures that async workers have immediate, structured access to the conversation history when executing heavy context processing.

## Consequences
*   **Positive:** Radically simplifies context window retrieval for the LiteLLM proxy. Workers can fetch a deterministic array of `Messages` using a single indexed `session_id` query.
*   **Positive:** Centralizes session lifecycle management within the backend, allowing [coreAI] to handle context decay and consolidation autonomously without client intervention.
*   **Positive:** Reuses battle-tested structural paradigms, reducing the risk of architectural dead ends.
*   **Negative:** Increases the initial relational footprint and the complexity of the first Alembic migration (Issue 2.1).
*   **Negative:** Requires strict application-level boundary enforcement to distinguish when an input should be logged as a generic `Event` versus a conversational `Message`.