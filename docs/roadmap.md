# CoreAI - Roadmap de Extracción y Despliegue

**Versión:** 1.2
**Estado:** Validado
**Objetivo:** MVP Operativo y Resiliente (Ciclo de 21 días).

---

## EPIC 1: Infraestructura y Persistencia Distribuida
- [ ] **1.1 Orquestación de Servicios**: Configuración de `docker-compose.yml` con PostgreSQL, Qdrant (Vector DB), Infinity (Embeddings/Rerank) y LiteLLM.
- [ ] **1.2 Migración de Capa Relacional**: Refactorización de esquemas de LifeOS a Postgres para gestión de tareas, documentos y metadatos estructurados.
- [ ] **1.3 Abstracción de Modelos**: Configuración de Infinity como motor de inferencia local para embeddings y LiteLLM como gateway.

## EPIC 2: Motor de Procesamiento y Resiliencia
- [ ] **2.1 Worker Engine (ADR-014)**: Implementación del pool de trabajadores asíncronos.
- [ ] **2.2 Gestión de Estado y Checkpointing**: Implementación de estados atómicos en Postgres con lógica de recuperación tras fallos (Protocolo Gabi).
- [ ] **2.3 Pipeline de Ingesta y Sincronización**: Procesado de archivos y sincronización coordinada: metadatos en Postgres y vectores en Qdrant.
- [ ] **2.4 Control de Integridad (Hashing)**: Detección de cambios vía SHA-256 para evitar duplicidad de procesado en ambos motores.

## EPIC 3: Interfaz y Protocolo MCP
- [ ] **3.1 Transporte SSE**: Endpoint `/sse` para soporte multi-cliente persistente.
- [ ] **3.2 Herramienta de Recuperación Semántica**: Tool MCP que consulta Qdrant y devuelve contexto enriquecido.
- [ ] **3.3 Recurso de Introspección**: Resource MCP que expone el esquema de Postgres para analítica estructural.