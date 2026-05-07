## EPIC 1: Infraestructura y Persistencia

### Story 1.1: Entorno de Ejecución con Separación de Responsabilidades
**Como** arquitecto de sistemas, **quiero** desplegar CoreAI con motores especializados, **para** que cada servicio gestione su dominio de datos (relacional vs vectorial) de forma aislada.
**Criterios de Aceptación (DoD):**
- [V] Docker Compose levanta Postgres, Qdrant e Infinity.
- [V] Qdrant es accesible vía API y tiene persistencia configurada en disco local.

### Story 1.2: Definición del Esquema Estructurado (Postgres)
**Como** sistema CoreAI, **quiero** usar Postgres exclusivamente para datos deterministas y estados de tareas, **para** garantizar la integridad referencial del sistema.
**Criterios de Aceptación (DoD):**
- [ ] Tablas de `documents` y `tasks` creadas en Postgres.
- [ ] La tabla `documents` contiene el ID de la colección/punto en Qdrant para mantener la trazabilidad.

---

## EPIC 2: Motor de Procesamiento y Resiliencia

### Story 2.1: Pipeline de Ingesta Coordinada (Postgres + Qdrant)
**Como** motor de conocimiento, **quiero** que el worker coordine la escritura en ambos motores, **para** asegurar que la metadata y los vectores estén sincronizados.
**Criterios de Aceptación (DoD):**
- [ ] El worker genera embeddings vía Infinity.
- [ ] El worker inserta el vector y metadatos en Qdrant.
- [ ] El worker registra el éxito y el hash del archivo en Postgres.

### Story 2.2: Checkpointing Atómico de Ingesta
**Como** usuario, **quiero** que el worker guarde el estado tras cada inserción en Qdrant, **para** no duplicar vectores si el proceso se reinicia bruscamente.
**Criterios de Aceptación (DoD):**
- [ ] Se implementa un "Savepoint" en Postgres tras cada bloque de vectores enviado a Qdrant.
- [ ] Al retomar una tarea `interrupted`, el sistema verifica qué fragmentos ya existen en Qdrant antes de procesar el siguiente.

---

## EPIC 3: Interfaz y Protocolo MCP

### Story 3.2: Herramienta MCP de Búsqueda (Qdrant Search)
**Como** modelo de lenguaje, **quiero** buscar información mediante la Tool MCP, **para** obtener resultados basados en la relevancia semántica de Qdrant.
**Criterios de Aceptación (DoD):**
- [V] La Tool `search_coreai` realiza una búsqueda `vector_search` sobre Qdrant.
- [ ] Se aplican filtros de payload en Qdrant (usando la metadata de Postgres) si la consulta lo requiere.