# ADR-007: Memoria Multimodal — Adopción Anticipada para la Demo ELIOS

## Status
Accepted — implementado (23 oct 2026; los tres sub-issues cerrados en main)

## Context and Problem Statement

coreAI es hoy un sustrato de memoria textual: la ingesta unificada (ADR-005) despacha tareas `VECTORIZE_EVENT` de contenido textual y la memoria unificada persiste registros relacionales con sus vectores de texto. El roadmap del sustrato situaba la memoria multimodal tras Epic 4, y el PRD de coreAI no incluye modalidad imagen en la fase actual.

La demo ELIOS (sprint 19 oct – ~1 nov) requiere identificación visual de piezas (FR-1/FR-2/FR-6 del PRD de elios-client): el edge client embebe imágenes vía Infinity (familia CLIP/SigLIP, dual-encoder) y esos vectores necesitan persistencia y recall con identidad propia (dimensión y espacio distintos de los de texto).

Esperar al roadmap implica no demostrar la identificación visual — la funcionalidad central del engagement actual.

**Pregunta:** ¿cómo incorpora coreAI el soporte de vectores de imagen que la demo necesita, sin torcer el roadmap del sustrato ni el PRD?

## Decision Outcome

Adopción anticipada y **acotada** de la memoria multimodal, como excepción justificada y documentada — el salto del roadmap y del PRD es explícito, no silencioso:

- Los image embeddings los sirve Infinity como tercer `--model-id` (familia CLIP/SigLIP; solo dual-encoder). El edge client llama a Infinity directamente — la spec OpenAI de embeddings no tiene modalidad imagen (excepción Infinity). ([ADR-007-001])
- Los vectores de imagen persisten en una **colección Qdrant separada** (dimensión/espacio propios). ([ADR-007-002])
- **Reglas del bypass cercado vigentes** hasta la extensión completa de este ADR: solo vectores de imagen; todo registro textual va por las tools MCP (`coreai_dispatch_task`); cada vector referencia el nodo coreAI del registro textual (fuente relacional de verdad — el cron de purga de huérfanos conserva sentido); expiry explícito — la deuda la absorbe la extensión del ADR en Fase 0/1.
- **Regla de decisión del sprint:** si el loop base llega a nivel de demo con tiempo restante, plumbing (passthrough genérico de vector pre-computado); si no, bypass cercado y la extensión del ADR queda para Fase 0/1.

Lo que este ADR **no** decide: campo `modality` en el schema de memoria, passthrough genérico, y el link pieza↔proyecto (decisión FR-3, aparte).

### Consequences

**Positivas:**
- La demo demuestra identificación visual sobre el sustrato real, no sobre mock.
- coreAI gana soporte de imagen temprano, acotado y documentado.
- El salto del roadmap queda explícito y con expiry.

**Negativas:**
- La línea demo corre por delante del PRD (deuda documentada).
- La colección de imágenes es infraestructura que el passthrough genérico posterior puede refactorizar.
- BUG-001 (#34, inicialización de Qdrant) puede tocar la creación de la colección.

## Post-implementación (23 oct 2026)

- **El bypass cercado quedó absorbido sin llegar a usarse.** El pipe de imagen va entero por el sustrato: `coreai_dispatch_task` (contrato `VectorizeImageEventPayload`) → event lane → handler → gateway LiteLLM (entrada `image-embedding`) → Infinity (`jina-clip-v2`) → colección `images`. Ningún vector de imagen salta las tools MCP: la regla «solo vectores de imagen por bypass» nunca llegó a producción y caduca como deuda.
- **Desviación respecto al texto de la decisión:** el edge client NO llama a Infinity directamente. La única ruta de embeddings de imagen implementada es la event lane a través del gateway LiteLLM (BUG-004 #58, PR #59): el proveedor infinity de LiteLLM mapea `modality` al endpoint de image embeddings, con enrutado y métricas del gateway.
- **La colección es garantía de arranque, no creación manual:** un despliegue limpio auto-genera `images` (1024 dims, coseno) — `ensure_collection` idempotente en el worker, antes de consumir la cola (PR #64).
- **Evento auto-anclado (decisión 22 oct):** la correlación texto↔imagen la da la sesión (`session_id` en el payload del punto), no una referencia encadenada a otra tarea (`text_event_id` eliminado). El `project` (FR-3 #51) viaja en el propio evento; la imagen en sí no se persiste: el vector es dato derivado.
- **E2E validado (22–23 oct):** stub de 99 bytes rebota SÍNCRONO en el dispatch con diagnóstico de bytes; imagen real (173 KB) → punto de 1024 dims en `images` con factura en `llm_audit`.

## Related
*   ADR-005 (#36) — Doctrina de Ingesta Unificada.
*   ADR-006 (#40) — Contratos de Tarea MCP.
*   Decisión FR-3 (#51) — link pieza↔proyecto (metadata vs DomainType).

---

# ISSUES

### [ADR-007-001] Infinity: Embeddings de Visión (3er `--model-id`) — #49
Cerrado (21 oct). Modelo elegido: `jinaai/jina-clip-v2` (dual-encoder EVA02-L + Jina-XLM-R, 1024 dims, 89 idiomas) sirviendo `image_embed` offline (`--model-id /data/clip_model`, `trust_remote_code`). Kit self-contained horneado por `scripts/prepare_clip_kit.py`; pin `transformers==4.51.3` como suelo y techo (el remote code importa `clip_loss`, eliminado en 4.52); `hf-modules/` gitignored y regenerable por script. Latencia CPU ~4,3 s/imagen → el diseño de reconocimiento on-demand queda confirmado (la captura de cámara solo cuando el usuario pregunta; los segundos-CPU aterrizan en el lane asíncrono).

### [ADR-007-002] Colección Qdrant de Imagen — #50 — ✅ Cerrado (PR #64, 23 oct)
Colección separada con dimensión/espacio propios (1024, similitud coseno). Garantizada al arranque del worker: `ensure_collection` idempotente por comparación de nombres (`src/repositories/vector.py`), invocada por `ensure_collections()` en `src/worker.py` ANTES de consumir la cola (backoff de arranque) y con `depends_on: qdrant` en el compose. Triage del BUG-001 (#34): no bloqueó — nota dejada en #34; las colecciones de texto por dominio quedan en el ámbito propio del bug.

### [ADR-007-003] Handler de vectorización de imágenes por event lane — #61 — ✅ Cerrado (PR #62, 23 oct)
Nuevo `TaskType` `VECTORIZE_IMAGE_EVENT` con contrato `VectorizeImageEventPayload` (data URI base64 raster-only; decodificabilidad validada en frontera con Pillow). El handler vectoriza vía gateway LiteLLM (entrada `image-embedding`, `modality=image` — BUG-004 #58, PR #59) y hace upsert en `images`: el punto ES la tarea; el payload ancla el vector a su `session_id` (correlación texto↔imagen por sesión) y a su `project` (FR-3 #51); la imagen no se persiste. E2E validado el 22 oct (stub de 99 bytes rebota síncrono; imagen real → punto de 1024 dims).
