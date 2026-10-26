# coreAI

**Sustrato local-first de orquestación LLM sobre Model Context Protocol (MCP).** Todo on-premise y empaquetado en Docker: PostgreSQL como fuente de verdad relacional, Qdrant como memoria vectorial derivada, un gateway LiteLLM para el plano de modelos y un córtex asíncrono que procesa las tareas de fondo. Los clientes — IDEs, agentes, clientes de borde — *brotan* del sustrato a través de MCP.

> coreAI es un **sustrato, no un cerebro**: el gateway enruta, no piensa; la orquestación interactiva vive en el cliente de borde; coreAI garantiza persistencia, memoria unificada y procesamiento de fondo.

## Principios arquitectónicos

- **Local-first / on-premise** — sin dependencia de nube; la soberanía de datos es parte del diseño, no un añadido.
- **100 % Docker** — el stack completo se levanta con un `docker compose up -d`.
- **Toda interacción externa es vía MCP** — sin REST, sin WebSockets, sin UI propia: los clientes consumen tools.
- **Memoria unificada** — PostgreSQL (UUIDs, entidades/eventos/relaciones) es la fuente de verdad; Qdrant contiene vectores derivados: nunca texto sin respaldo relacional.
- **Ingesta unificada (ADR-005)** — una única apertura de escritura, `coreai_dispatch_task`: las escrituras son eventos que el córtex procesa en asíncrono; las lecturas síncronas acotadas (`coreai_search_context` + `coreai_search_image`, read-only) son la única otra apertura.
- **Contratos de tarea (ADR-006)** — cada tipo de tarea despachable lleva contrato formal registrado: *sin contrato no hay despacho*.
- **Memoria multimodal acotada (ADR-007)** — la ingesta de imagen va por la event lane como toda escritura (tarea `vectorize_image_event` con contrato ADR-006); sus vectores viven en una colección Qdrant propia (jina-clip-v2, 1024 dims) garantizada al arranque del worker.
- **Córtex asíncrono sin broker** — cola de tareas sobre PostgreSQL con `SELECT ... FOR UPDATE SKIP LOCKED`.

## Arquitectura

```mermaid
flowchart LR
    CLIENT["Cliente de borde<br/>(Cline / IDE / agente)"]
    subgraph DOCKER["coreAI · docker compose"]
        MCP["Servidor MCP<br/>(STDIO · docker exec -i)"]
        PG[("PostgreSQL<br/>fuente de verdad + cola de tareas")]
        WORKER["Worker · Córtex Asíncrono"]
        QDRANT[("Qdrant<br/>vectores derivados")]
        INFINITY["Infinity · embeddings<br/>texto (e5 · NLI) · visión (jina-clip-v2)"]
        LITELLM["Gateway LiteLLM :4000"]
        LLM["LLM local (LM Studio)"]
    end
    CLIENT -- "tools/call · dispatch · consulta de estado" --> MCP
    MCP -- "task PENDING" --> PG
    WORKER -- "claim (SKIP LOCKED)" --> PG
    WORKER -- "evento + memoria" --> PG
    WORKER -- "embedding (texto · imagen)" --> INFINITY
    WORKER -- "upsert vector" --> QDRANT
    WORKER -- "LLM (consolidación)" --> LITELLM
    LITELLM --> LLM
    CLIENT -- "chat (API OpenAI-compatible)" --> LITELLM
```

| Servicio | Contenedor | Puerto | Función |
|---|---|---|---|
| postgres | `coreai_postgres` | 5432 | Fuente de verdad relacional + cola de tareas (`pgvector/pgvector:pg15`) |
| qdrant | `coreai_qdrant` | 6333 | Memoria vectorial derivada (colecciones por dominio; colección de imagen propia — ADR-007) |
| litellm | `coreai_litellm` | 4000 | Gateway OpenAI-compatible: enrutado de modelos, logs y métricas |
| infinity | `coreai_infinity` | 8080 | Embeddings locales servidos desde `/data`: texto (`e5-small`), NLI (DeBERTa-v3) e imagen (`jina-clip-v2`, ADR-007) |
| core-mcp | `coreai_mcp` | — | Lanzadera: el servidor MCP se spawnea por cliente vía `docker exec -i` (STDIO, sin red) |
| worker-node | `coreai_worker` | — | Córtex asíncrono: reclama tareas y ejecuta los handlers |
| adminer | `coreai_adminer` | 8081 | Administración de la base de datos |
| loki / prometheus / grafana | `coreai_loki` / `coreai_prometheus` / `coreai_grafana` | 3100 (solo loopback) / — / 3000 | Observabilidad: logs, métricas y dashboards |

## Arranque rápido

```bash
# 1. Variables de entorno
cp .envExample .env    # revisa POSTGRES_URL y credenciales

# 2. Stack completo
docker compose up -d

# 3. Tests (requieren la BD de test: el conftest aborta si la URL no apunta a coreai_test_db)
pytest
```

**Prerrequisitos:**
- Docker + Docker Compose.
- Modelos: Infinity sirve embeddings de texto e imagen desde `/data` (ver `scripts/prepare_model.py` — el kit CLIP de visión lo prepara `scripts/prepare_clip_kit.py`); el chat apunta al gateway LiteLLM (`:4000`, config en `src/config/litellm_config.edge.yaml`), que enruta hacia tu modelo local.

**Conectar un cliente MCP** (ej. Cline, `cline_mcp_settings.json`):

```json
{
  "mcpServers": {
    "coreai": {
      "command": "docker",
      "args": ["exec", "-i", "coreai_mcp", "python", "-m", "src.main"]
    }
  }
}
```

> La `-i` es imprescindible: sin ella stdin no se conecta y el servidor muere de EOF (el cliente ve 0 tools).

## Superficie MCP actual

| Tool | Estado | Descripción |
|---|---|---|
| `coreai_dispatch_task` | ✅ Operativa | Única apertura de escritura: inyecta una tarea asíncrona (contrato del tipo obligatorio — ADR-006) con auto-vivificación de sesión/entidad si no se aportan IDs. |
| `coreai_get_task` | ✅ Operativa | Consulta read-only del estado de una tarea (`task_id` → status + `error_trace`): la lectura write-then-read de la doctrina ADR-005. Respuesta pelada — el payload crudo nunca viaja. |
| `coreai_search_context` | ✅ Operativa | Lectura síncrona read-only (ADR-005-002): búsqueda semántica sobre TODAS las colecciones de texto, merge global por score y top_k tras el merge. Sin resultados sobre el umbral = no-match honesto. La consistencia con las ingestas es eventual. |
| `coreai_search_image` | ✅ Operativa | Lectura síncrona read-only (identify, ADR-007): similitud visual contra la colección de imágenes con embed efímero de consulta — la imagen de consulta nunca se persiste. Misma superficie de filtros que el recall de texto (proyecto, top_k, umbral de visión). |

## Mapa del repositorio

```
src/
  config/        # litellm_config.edge.yaml, provisioning de Prometheus/Grafana
  contracts/     # Registro central de contratos de tarea (ADR-006-001)
  db/            # SQLAlchemy: modelos, sesiones, repositorios (relacional + vectorial)
  managers/      # TaskManager, HybridMemoryManager, LLMClient, ConfigManager
  mcp/tools/     # Superficie MCP (coreai_dispatch_task, coreai_get_task, coreai_search_context, coreai_search_image)
  schemas/       # Contratos Pydantic: tasks, memory, llm, recall
  workers/       # Router del córtex: TASK_REGISTRY + handlers por tipo de tarea
  main.py        # Servidor MCP STDIO ("sin red, sin FastAPI")
  worker.py      # Bucle del worker
docs/            # PRD, vision, moscow, roadmap (es/en) + adr/
tests/           # Suite pytest (BD de test protegida por cortafuegos en conftest)
scripts/         # prepare_model (+ prepare_clip_kit), schedulers/crontab
```

> ⚠️ `scripts/db/` es material legacy desincronizado con los modelos actuales — no re-ejecutar contra una base de datos viva.

## Documentación

- **Producto:** [PRD (es)](docs/PRD_CoreAI_es.md) · [PRD (en)](docs/PRD_CoreAI_en.md) · [Vision Document (es)](docs/vision_document_es.md) · [Vision (en)](docs/vision_document_en.md) · [MoSCoW (es)](docs/moscow_es.md) · [MoSCoW (en)](docs/moscow_en.md) · [Roadmap](docs/roadmap.md)

**Decisiones de arquitectura (ADR):**

| ADR | Decisión |
|---|---|
| [001](docs/adr/001-added-entities.md) | Added Entities (única prosa en inglés — legado previo a la convención) |
| [002](docs/adr/002-removed-fallbacks.md) | Removed Fallbacks |
| [003](docs/adr/003-base-observability.md) | Base Observability |
| [004](docs/adr/004-mcp-tracer-bullet.md) | Tracer Bullet vía MCP (con post-mortem técnico) |
| [005](docs/adr/005-unified-ingestion-doctrine.md) | Doctrina de Ingesta Unificada — escrituras vía event lane; apertura síncrona read-only acotada: recall de texto e imagen (✅ implementado, 26 oct) |
| [006](docs/adr/006-mcp-contracts.md) | Contratos de Tarea MCP — validación síncrona en la frontera y exposición como resources (✅ implementado, 23 oct) |
| [007](docs/adr/007-multimodal-memory.md) | Memoria Multimodal — adopción anticipada y acotada (identificación visual, demo ELIOS) (✅ implementado, 23 oct) |

## Estado del proyecto

En desarrollo activo. El **Epic 4: Córtex Asíncrono** (issue #26) está en main desde el 21 de octubre — el sustrato expone `coreai_dispatch_task` + `coreai_get_task` más el recall read-only síncrono (`coreai_search_context` + `coreai_search_image`, [ADR-005-002] #37, 26 oct: sweep por dominios, filtro por proyecto y embed efímero de consulta) — y el epic permanece abierto hasta que aterricen los pospuestos (4.3 #29, 4.4 #30). El **ADR-006 (contratos de tarea)** y el **ADR-007 (memoria multimodal)** están implementados y validados E2E (23 oct): tres contratos registrados y publicados como resources, y la tubería de imagen completa — dispatch → contrato → worker → gateway LiteLLM → colección `images` — viva en main.

---

*Convención del repo: prosa en español; cabeceras estructurales (Status, Context and Problem Statement, Decision Outcome, Consequences) en inglés; slugs de archivos en inglés.*
