# Vision Document: CoreAI - Persistent Contextual Infrastructure & Hybrid Memory Layer

## 1. Declaration of Intentions
**CoreAI** is an infrastructure solution designed to address the volatility of context in AI systems. Its primary goal is to provide a persistent memory backend that is independent of user interfaces (IDEs, chat applications) and language models (LLMs).

The system assumes that intelligence is a **"commodity"** that can be swapped, while the real value lies in the structure, veracity, and availability of local context.

---

## 2. Genesis & Technical Justification
CoreAI emerges from abstracting high-fidelity industrial-grade components previously validated in automation environments (e.g., lifeOS). It leverages a **resilience stack**—comprising relational state management, vector memory, and background task orchestration—to deliver an agnostic service core.

---

## 3. System Architecture ("The Power Stack")
CoreAI operates as an orchestrated service ecosystem, ensuring that data "digestion" does not interfere with client response times.

### A. Persistent Service Layer (The Core)
- **Hybrid Memory**:
  - PostgreSQL for structured facts and metadata.
  - `pgvector`/`Infinity` for semantic retrieval.
- **Asynchronous Synchronization** (Background Workers):
  Based on ADR-014, ingestion and re-hashing are offloaded to independent workers, ensuring context remains up-to-date without CPU penalties in the main process.
- **Inference Abstraction**:
  Uses `LiteLLM`/`Infinity` to centralize reasoning and embedding requests, enabling seamless switching between local models.

### B. Interface Layer: MCP via SSE
Unlike ephemeral STDIO-based MCP servers, CoreAI exposes itself via **Server-Sent Events (SSE)** over HTTP, enabling:
- **Lifetime Independence**: The CoreAI server remains active and processing data even when clients (IDE/Agent) are closed.
- **Multi-client Support**: A single CoreAI node can serve context to multiple interfaces simultaneously.
- **Persistent State**: Context is retained across UI restarts.

---

## 4. Defining Contextual Capabilities
CoreAI standardizes how AI "understands" its environment by decoupling two MCP protocol concepts:

| Concept          | Description                                                                                     |
|------------------|-------------------------------------------------------------------------------------------------|
| **Resources**    | Truth anchors: Structured DB schemas, knowledge graphs, and historical records (read-only).   |
| **Tools**        | Operational capabilities: Semantic search (RAG), record filtering, and re-indexing triggers.  |

---

## 5. Design Objectives (Key Results)
- **Offline-First Resilience**: 100% offline operation, eliminating API quota dependencies for privacy.
- **Zero-Latency UI Updates**: Background workers ensure real-time context updates without UI lag.
- **Purpose-Portable Deployment**: A single CoreAI binary can deploy for software dev, legal research, or personal knowledge bases via configurable ingestion settings.

---