# CoreAI - Roadmap de Extracción y Despliegue

**Versión:** 1.2
**Estado:** Validado
**Objetivo:** MVP Operativo y Resiliente (Ciclo de 21 días).

---

## EPIC 1: Infraestructura y Persistencia Distribuida
- [x] **1.1 Orquestación de Servicios**: Configuración de `docker-compose.yml` con PostgreSQL, Qdrant (Vector DB), Infinity (Embeddings/Rerank) y LiteLLM. ✅ Evidencia: `docker-compose.yml` define servicios completos con healthchecks, persistencia en volúmenes, dependencias entre servicios y comandos de inicio.
- [ ] **1.2 Migración de Capa Relacional**: Refactorización de esquemas de LifeOS a Postgres para gestión de tareas, documentos y metadatos estructurados.
- [x] **1.3 Abstracción de Modelos**: Configuración de Infinity como motor de inferencia local para embeddings y LiteLLM como gateway. ✅ Evidencia: `src/managers/memory_manager.py` delega generación de embeddings vía `AsyncOpenAI` a `LITELLM_URL`, con soporte en `docker-compose.yml` para ambos servicios (Infinity en puerto 8080, LiteLLM en 4000).

## EPIC 2: Motor de Procesamiento y Resiliencia
- [ ] **2.1 Worker Engine (ADR-014)**: Implementación del pool de trabajadores asíncronos.
- [ ] **2.2 Gestión de Estado y Checkpointing**: Implementación de estados atómicos en Postgres con lógica de recuperación tras fallos (Protocolo Gabi).
- [ ] **2.3 Pipeline de Ingesta y Sincronización**: Procesado de archivos y sincronización coordinada: metadatos en Postgres y vectores en Qdrant.
- [ ] **2.4 Control de Integridad (Hashing)**: Detección de cambios vía SHA-256 para evitar duplicidad de procesado en ambos motores.

## EPIC 3: Interfaz y Protocolo MCP
- [x] **3.1 Transporte SSE**: Endpoint `/sse` para soporte multi-cliente persistente. ✅ Evidencia: `src/main.py` define `@app.get("/sse")` con `SseServerTransport` para canal JSON-RPC.
- [x] **3.2 Herramienta de Recuperación Semántica**: Tool MCP que consulta Qdrant y devuelve contexto enriquecido. ✅ Evidencia: `src/mcp/tools/vector_tools.py` registra tool `coreai_search_context` que invoca `VectorMemoryManager.search_memory()`.
- [ ] **3.3 Recurso de Introspección**: Resource MCP que expone el esquema de Postgres para analítica estructural.

---

## 📊 RESUMEN DE PROGRESO
| Epic | Completado | Pendiente |
|------|------------|-----------|
| **EPIC 1** | ✅ 2/3 (67%) | — |
| **EPIC 2** | ❌ 0/4 (0%) | — |
| **EPIC 3** | ✅ 2/3 (67%) | — |
| **Total** | **5/10 (50%)** | — |

---