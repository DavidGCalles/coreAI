# CoreAI - Product Requirements Document (PRD)

**Version:** 1.0  
**Status:** Draft  
**Last Updated:** $(date +%Y-%m-%d)  

---

## 1. Executive Summary

CoreAI is a persistent memory backend infrastructure designed to solve context volatility in AI systems. It provides an IDE/model-independent, offline-first service that combines relational and vector memory with asynchronous task processing via the Model Context Protocol (MCP).

**Core Value Proposition:** Intelligence is commoditized; structure, veracity, and local availability are valuable.

---

## 2. Problem Statement

### Current Pain Points
- **Context Volatility**: AI systems lose context when IDEs/chat interfaces restart or disconnect.
- **Blocking Operations**: Data ingestion and embedding calculations block the main inference server.
- **Single-Purpose Deployment**: Generic RAG plugins lack structured schema exposure and multi-format support.
- **API Dependencies**: External APIs introduce latency, quota limits, and privacy concerns.

### Desired State
A sovereign, persistent memory layer that:
- Updates asynchronously without blocking inference
- Exposes database schemas as deterministic resources
- Supports multimodal ingestion (Markdown, JSON, SQL)
- Operates 100% offline with local model abstraction

---

## 3. Vision & Intentions

### Core Philosophy
> "The real value lies in the structure of context, its veracity, and local availability."

### System Assumptions
| Assumption | Rationale |
|------------|-----------|
| Intelligence is a commodity | LLMs can be swapped; infrastructure remains stable |
| Local context is valuable | Privacy and sovereignty require no cloud sync |
| Persistence outlives UI | Context must survive IDE/chat restarts |

### Genesis & Justification
CoreAI abstracts industrial-grade components from high-fidelity automation environments (e.g., lifeOS), implementing a resilience stack of:
- Relational state management (**PostgreSQL**)
- **Vector memory retrieval (Qdrant)** - Dedicated vector store with HNSW index and payload filtering for semantic search
- Background task orchestration (**Worker Engine**)

---

## 4. Architecture Overview

### 4.1 Persistent Service Layer (The Core)

```
┌─────────────────────────────────────────────────────────────┐
│                    CoreAI Service                           │
├─────────────────────────────────────────────────────────────┤
│  ┌──────────────────┐    ┌──────────────────┐              │
│  │   PostgreSQL     │    │   Qdrant       │              │
│  │  (Relational)    │◄──►│  (Vector Store)│              │
│  │  - Facts         │    │  - Embeddings  │              │
│  │  - Metadata      │    │  - Semantic    │              │
│  └──────────────────┘    └────────────────┘              │
│                           ▲                                │
│                           │ Workers (ADR-014)               │
│           ┌──────────────┼──────────────┐                 │
│           ▼              ▼              ▼                 │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐        │
│  │ Ingestion   │  │ Re-hash     │  │ Embedding   │        │
│  │ Worker      │  │ Worker      │  │ Worker      │        │
│  └─────────────┘  └─────────────┘  └─────────────┘        │
├─────────────────────────────────────────────────────────────┤
│  Inference Abstraction: LiteLLM + Infinity                  │
│  (Transparent LLM switching)                                │
└─────────────────────────────────────────────────────────────┘
```

**Key Components:**
| Component | Technology | Purpose |
|-----------|------------|---------|
| Relational DB | PostgreSQL | Structured facts, metadata, history |
| Vector Store | Qdrant | Semantic retrieval |
| Embeddings server | Infinity | Serving embedding model, classifier and reranker|
| Task Queue | ADR-014 Workers | Async ingestion/re-indexing |
| Inference Proxy | LiteLLM | Centralized LLM routing |

### 4.2 Interface Layer: MCP via SSE

Unlike ephemeral STDIO-based servers, CoreAI exposes itself via **HTTP/SSE**:

| Feature | Benefit |
|---------|---------|
| Persistent Lifecycle | Server remains active when clients disconnect |
| Multi-client Support | Single node serves multiple IDEs/chats simultaneously |
| State Persistence | Context survives UI restarts |
| HTTP-based | Standard protocol, easy to proxy/load-balance |

### 4.3 MCP Protocol Mapping

CoreAI decouples MCP concepts for clarity:

| MCP Concept | CoreAI Implementation | Characteristics |
|-------------|----------------------|-----------------|
| **Resources** | Schema Resources | Read-only, deterministic structure (DB schemas, graphs) |
| **Tools** | Search & Action Tools | RAG search, filtering, re-indexing triggers |

---

## 5. Functional Requirements

### 5.1 Must Have (Critical Path)

#### FR-001: Docker Compose Infrastructure
- [ ] Orchestrate PostgreSQL (**without pgvector extension**)
- [ ] **Deploy Qdrant** for high-performance vector embeddings and semantic search
- [ ] Integrate LiteLLM as inference proxy
- [ ] Define minimal service dependencies in `docker-compose.yml`

#### FR-002: MCP Server via SSE
- [ ] Implement HTTP/SSE endpoint (`/sse`)
- [ ] Support multiple concurrent client connections
- [ ] Maintain persistent state across connection drops
- [ ] Expose MCP-compatible JSON-RPC over SSE stream

#### FR-003: Worker Engine (ADR-014)
- [ ] Create background worker pool for async tasks
- [ ] Implement ingestion queue for file/document processing
- [ ] Build embedding calculation workers
- [ ] Ensure main inference server remains unblocked

#### FR-004: Semantic Search Tool
- [ ] Expose `search` MCP tool with vector query support
- [ ] Support hybrid search (keyword + semantic)
- [ ] Return ranked results with metadata
- [ ] Implement relevance scoring

#### FR-005: Schema Resource
- [ ] Expose database schema as MCP resource
- [ ] Support relational and document-based structures
- [ ] Provide deterministic, versioned schema representation
- [ ] Enable model introspection of data structure

#### FR-006: Hashing Management
- [ ] Implement file/entity hashing (SHA-256 or similar)
- [ ] Detect changes without full re-indexing
- [ ] Skip processing unchanged content
- [ ] Maintain change history for audit trails

### 5.2 Should Have (Power Features)

#### FR-007: File System Watcher
- [ ] Integrate `inotify`/`FSEvents`/`ReadDirectoryChangesW`
- [ ] Trigger workers on file save/change events
- [ ] Debounce rapid successive changes
- [ ] Support recursive directory monitoring

#### FR-008: Multimodal Ingestion
- [ ] Parse Markdown with hierarchical structure preservation
- [ ] Extract and index JSON schemas
- [ ] Handle SQL schema files (`.sql`, `.db`)
- [ ] Support nested/recursive document structures

#### FR-009: Admin CLI
- [ ] `coreai init` - Initialize system from scratch
- [ ] `coreai purge` - Clear vector database safely
- [ ] `coreai reindex --force` - Trigger full re-indexation
- [ ] `coreai status` - Display worker/DB health

#### FR-010: Local Reranking
- [ ] Integrate lightweight reranker model (e.g., BGE-Reranker)
- [ ] Apply before sending context to LLM
- [ ] Configurable reranking threshold
- [ ] Support multiple reranker models

### 5.3 Could Have (Nice-to-Have Enhancements)

#### FR-011: Graph Memory Layer
- [ ] Implement Neo4j-like graph storage on PostgreSQL
- [ ] Extract entity relationships from text
- [ ] Query complex entity graphs
- [ ] Visualize knowledge connections

#### FR-012: Observability Dashboard
- [ ] Minimal web UI for system monitoring
- [ ] Real-time worker status indicators
- [ ] Vector memory usage metrics
- [ ] MCP request/response logs

#### FR-013: Context Auto-Selection
- [ ] Analyze query intent automatically
- [ ] Select relevant resources without model prompting
- [ ] Learn from user feedback
- [ ] Optimize context window usage

#### FR-014: At-Rest Encryption
- [ ] Encrypt sensitive data in PostgreSQL
- [ ] Key management via environment variables
- [ ] Transparent encryption/decryption layer
- [ ] Audit log for access attempts

### 5.4 Won't Have (Out of Scope)

| Feature | Rationale |
|---------|-----------|
| Native Chat UI | CoreAI is backend-only; clients provide UI |
| Model Hosting | Users manage LLMs via LiteLLM/LM Studio/Ollama |
| Cloud Sync | Strictly local/sovereign by design |
| Complex Multi-tenancy | Single-instance-per-user model |

---

## 6. Non-Functional Requirements

### 6.1 Performance Targets

| Metric | Target | Measurement Method |
|--------|--------|-------------------|
| Ingestion Latency | < 5s per document | Worker execution time |
| Search Latency | < 200ms | P95 response time |
| Memory Usage | < 4GB (default config) | System monitoring |
| Concurrent Clients | > 10 simultaneous | Load testing |

### 6.2 Reliability Requirements

- **Offline-First**: 100% operational without network
- **Data Durability**: ACID compliance for PostgreSQL operations; Qdrant with persistence mode
- **Worker Fault Tolerance**: Automatic retry on worker failure with Qdrant savepoints
- **Recovery Time Objective (RTO)**: < 5 minutes after restart

### 6.3 Security Requirements

- **Local-Only**: No data leaves the machine
- **Input Sanitization**: Prevent injection attacks in MCP tools
- **Resource Isolation**: Each instance is purpose-specific
- **Audit Logging**: Track all schema/resource accesses

---

## 7. Technical Constraints & Assumptions

### 7.1 Technology Stack
| Layer | Technology | Justification |
|-------|------------|---------------|
| Database | PostgreSQL | Mature, ACID-compliant relational database |
| **Vector Store** | **Qdrant** | **Dedicated vector database with optimized HNSW index and payload filtering** |
| Inference Proxy | LiteLLM | Universal LLM abstraction |
| MCP Transport | HTTP/SSE | Persistent, standard protocol |
| Background Tasks | Python asyncio/queue | Simple, reliable worker pattern |
| File Watching | Platform-native (`inotify`, etc.) | Low overhead event detection |

### 7.2 Environment Assumptions
- Linux/macOS/Windows support (via Docker)
- Minimum 4GB RAM for vector store + workers
- Network access only to LLM hosting (not CoreAI itself)
- User manages their own LLM infrastructure

---

## 8. Success Metrics & Key Results

### 8.1 Quantitative Metrics

| Metric | Target | Definition |
|--------|--------|------------|
| Context Freshness | 95%+ within 24h | % of ingested data re-indexed timely via Qdrant |
| Search Precision | > 0.75 F1 score | **Qdrant HNSW search accuracy** with cosine similarity |
| Worker Uptime | > 99.5% | Background task availability with checkpointing |
| Latency (Search) | < 300ms p95 | End-to-end search response via Qdrant API |

### 8.2 Qualitative Outcomes

- ✅ Zero external API dependencies for context storage (**Qdrant local-only**)
- ✅ Absolute privacy of local knowledge base
- ✅ Seamless switching between LLM providers (LiteLLM)
- ✅ Context survives application restarts (**Qdrant persistence enabled**)
- ✅ Multi-format document support out-of-the-box

---

## 9. Implementation Roadmap

### Phase 1: Foundation (MVP)
- [ ] Docker Compose infrastructure setup
- [ ] MCP SSE server implementation
- [ ] Basic worker engine with ingestion queue
- [ ] Semantic search tool + schema resource
- [ ] Hashing change detection

**Duration:** 4-6 weeks  
**Deliverable:** Functional CoreAI service with basic RAG capabilities

### Phase 2: Power Features
- [ ] File system watcher integration
- [ ] Multimodal ingestion (Markdown, JSON, SQL)
- [ ] Admin CLI tools
- [ ] Local reranking model

**Duration:** 4 weeks  
**Deliverable:** Production-ready CoreAI with auto-ingestion and improved precision

### Phase 3: Polish & Scale
- [ ] Graph memory layer (optional)
- [ ] Observability dashboard
- [ ] Context auto-selection logic
- [ ] At-rest encryption (optional)

**Duration:** 4 weeks  
**Deliverable:** Enterprise-grade CoreAI with advanced features

---

## 10. Risk Analysis

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Worker failures cause data loss | Medium | High | Implement checkpointing and retry logic with Qdrant savepoints |
| Vector store memory exhaustion | **Low (Qdrant optimized)** | Medium | Add memory limits and use Qdrant's collection policies |
| LLM provider downtime affects inference | Medium | Medium | Graceful degradation with cached responses |
| File watcher platform incompatibility | Low | Medium | Abstract to cross-platform abstraction layer |

---

## 11. Appendix

### A. Glossary
- **MCP**: Model Context Protocol - Standard for AI agent communication
- **SSE**: Server-Sent Events - HTTP-based push technology
- **RAG**: Retrieval-Augmented Generation - AI pattern using external knowledge
- **Qdrant**: **Dedicated vector database for semantic search and embeddings** 
- **Infinity**: High-performance vector database (optional)

### B. References
- [MCP Specification](https://modelcontextprotocol.io/)
- [ADR-014: Worker Engine Design](./docs/adr-014.md)
- [Vision Document](./vision-document.md)
- [Requirements (Spanish)](./docs/requisitos_coreai.md)

---

**Document Control**  
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | $(date +%Y-%m-%d) | CoreAI Team | Initial PRD creation |
