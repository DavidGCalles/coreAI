# 🟢 Must Have (Essential for CoreAI to function)

Without this, there is no system—just a loose script.

## Base Infrastructure (Docker Compose)
Orchestration of PostgreSQL (with pgvector), Infinity (embeddings), and LiteLLM. This forms the minimal framework.

## MCP Server via SSE
Implementation of the MCP interface over HTTP/SSE to allow CoreAI to be a persistent, IDE-independent service.

## Worker Engine (ADR-014)
A queue system for background processing of ingestion and embedding tasks, preventing blocking of the main server.

## Semantic Search Tool
A robust "Tool" MCP that performs vector searches against the local database.

## Schema Resource (Resource)
An MCP "Resource" that deterministically exposes the database structure (relational or document-based) to the model.

## Hashing Management
Logic to detect changes in files/entities and avoid unnecessary re-indexing of unchanged content.

---

# 🟡 Should Have (What gives CoreAI real power)

What sets CoreAI apart from a typical RAG plugin:

## File System Watcher
Integration with an event observer (`on_save`, `on_change`) that automatically triggers tasks to the Workers.

## Multimodal Support
Ability to ingest not only plain text but hierarchical structures like Markdown, JSON, and SQL schemas.

## Admin CLI
Command-line tools for initializing the system, purging the database, or forcing a bulk re-indexation.

## Local Reranking
Integration of a lightweight reranking model to improve precision of RAG results before sending them to the main model.

---

# 🔵 Could Have (Performance Enhancements)

What makes the system "sexy" and ultra-efficient:

## Graph Memory Layer
Implementation of a graph layer over PostgreSQL to understand complex relationships between entities (not just semantic similarity).

## Observability Dashboard
A minimal web interface for monitoring Worker status, vector memory usage, and request logs.

## Context Auto-Selection
Logic allowing CoreAI to automatically decide which Resources to send to the model based on query intent (without manual prompting by the model).

## At-Rest Encryption
Local security layer for sensitive data stored in the vector database.

---

# 🔴 Won't Have (Out of Scope)

To prevent the "Cathedral" from becoming unmanageable:

## Native Chat Interface
CoreAI is a backend; UI is provided by clients like Continue, VS Code, or Telegram.

## Model Management (Hosting)
CoreAI does not host LLMs; it connects to them via LiteLLM. The user is responsible for their LM Studio or Ollama instance.

## Cloud Synchronization
No "CoreAI cloud." Persistence is strictly local and sovereign by design.

## Complex Multi-tenancy
The system operates as a single-purpose/user instance; not designed for multi-user SaaS with complex roles/permissions.