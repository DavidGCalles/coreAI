# Product Requirements Document (PRD): [coreAI] - Base Architecture Level

## 1. System Nature (The Core Engine)
[coreAI] is a transactional backend for asynchronous cognition and state management. It acts as the central nervous system (headless) for any external agent or client. Its function is not to "do things," but to maintain reality coherence (data), process context in the background, and expose a standardized interface for others to operate.

## 2. Memory Subsystem (Hybrid Relational-Vector Graph)
Memory is not just storage; it's an entity resolution and temporal context engine.

*   **Relational Engine (PostgreSQL):** Absolute custodian of truth. Defines the hard schema:
    *   *Entity Registry:* System nodes (people, projects, concepts, infrastructure devices).
    *   *Episodic History:* Immutable log of all transactions, interactions, and telemetry.
    *   *Access Control (RBAC):* Authentication and permissions at the database level for clients/agents consuming the API.
*   **Semantic Engine (pgvector/Vector DB):** Linked via foreign keys (UUIDs) to relational entities. Enables retrieval by conceptual similarity.
*   **Decay and Compression:** Native database logic to compress old events into "consolidated memories," freeing context space without losing traceability.

## 3. Protocol Gateway (MCP Registry)
[coreAI] does not hardcode tools. It acts as a dynamic *Registry* under the Model Context Protocol (MCP) standard.

*   **Dynamic Capability Registration:** The system exposes an endpoint where external services (or internal modules) register their capabilities (manifests).
*   **I/O Orchestration:** Whether controlling a hypervisor, auditing local network logs, or reading physical sensors, the client sees only a unified MCP server. [coreAI] validates permissions and routes the request to the corresponding tool.
*   **Isolation:** If a tool fails, the transactional core of [coreAI] remains unaware.

## 4. Async Cortex (Event-Driven Background Processing)
The system cannot depend on a client making a *request* to think. It must be event-driven (Event Bus / Message Queue).

*   **Ingestion Pipelines:** When new information is injected (a log, an interaction), an event is queued.
*   **Decoupled Workers:** Async consumers pick up events to:
    *   Extract new entities and update the relational graph.
    *   Generate embeddings and dump them into the vector database.
    *   Audit data inconsistencies.
*   **Maintenance Routines:** Heavy scheduled tasks for index reorganization and nightly compression.

## 5. Sovereign Inference Routing (LLM Proxy)
[coreAI] is agnostic to the cognitive engine, but prioritizes data sovereignty.

*   **Transparent Proxy (LiteLLM):** All inference traffic passes through a controlled funnel.
*   **Local-First Balancing:** Internal tasks of the *Async Cortex* (entity extraction, summarization) are routed mandatorily to the local hardware cluster (open-source models).
*   **External Fallback:** Only if computational load demands it or local hardware is unavailable does the proxy derive the request to external APIs, leaving an audit trail and cost in the relational database.