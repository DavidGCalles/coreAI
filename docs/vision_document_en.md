# Vision Document: [coreAI]

> **Revision status (Nov 2026):** aligned with the repo's current doctrine (ADR-001 through ADR-012). Changes from the foundational version, with their reasons, live in the ADRs and in section 6 of the PRD.

## 1. Nature and Paradigm
[coreAI] is not an application; it is sovereign infrastructure. It is conceived as the transactional and cognitive base layer for any ecosystem of automations, agents, or future interfaces. Its purpose is to act as the immutable Source of Truth engine of a system, isolating the complexity of hybrid storage, inference routing, and asynchronous processing inside a hermetic core. It is a **substrate, not a brain**: the gateway routes, it does not think; interactive orchestration lives in the edge client; [coreAI] guarantees persistence, unified memory, and background processing.

## 2. Sovereignty and Agnosticism
The system prioritizes absolute control of data and the execution cycle, rejecting critical external dependencies.
*   **Local-First by Design:** Architected to deploy and operate with maximum efficiency on owned infrastructure and dedicated hardware, keeping sensitive data and heavy inference workloads inside the local perimeter.
*   **Interface Agnosticism:** [coreAI] is strictly *headless*. It actively ignores how its data is consumed or who requests it, delegating presentation, swarm orchestration, and interaction to external clients under strict authorization rules.
*   **Model Agnosticism:** The system's cognition is fully decoupled from the inference provider. Through an integrated proxy, it guarantees survival against obsolescence, service outages, or policy changes of any external LLM.

## 3. Unified Memory Architecture
Persistence transcends the traditional database concept to become an engine of temporal and relational context resolution.
*   **Structural Determinism:** A robust relational database (PostgreSQL) acts as the anchor of the system's reality. It guarantees the immutability of the event log, referential integrity, and entity topology.
*   **Semantic Abstraction:** The vector layer (Qdrant) operates in strict subordination to the relational one. It provides similarity retrieval and context expansion, but every vector is an appendix of a deterministic record, eradicating the fragmentation of truth.
*   **Identity and Ownership:** The substrate knows who dispatches: declarative entities governed at deployment, a traceability actor on every task, and per-record ownership with visibility respected in recall (ADR-012). The full permission model (RBAC) is explicitly deferred, and will consume this identity.

## 4. Capability Standardization (MCP Protocol)
The system rejects coupling and *ad-hoc* integrations. Every read, write, or query is exposed to the core exclusively through the Model Context Protocol (MCP) standard: a fixed surface of tools with formal contracts, validated at the frontier and published as resources (ADR-006). [coreAI] scales its capabilities additively, through deliberate review (ADR + PR), without altering its foundational code. *(Change from the foundational version: dynamic hot registration of tools was discarded in favor of the stability of the contract tenants consume.)*

## 5. Autonomous Cortex (Event-Driven)
[coreAI] abandons the limitation of the reactive (request-response) model. It implements an async cortex over a native relational queue (PostgreSQL with `SELECT ... FOR UPDATE SKIP LOCKED`, no external broker) with background consumers that let the system work in the shadows: it consolidates and compresses episodic memories, corrects the metadata of what is already stored without re-embedding (ADR-009), and keeps per-attempt audit trail in the DLQ. The system structures its own state even without external stimuli. *(Change from the foundational version: the "event bus" materialized as a native PostgreSQL queue — strong consistency and resilience without one more piece to operate.)*
