# ADR-007: Memoria Multimodal — Adopción Anticipada para la Demo ELIOS

## Status
Proposed

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

## Related
*   ADR-005 (#36) — Doctrina de Ingesta Unificada.
*   ADR-006 (#40) — Contratos de Tarea MCP.
*   Decisión FR-3 (#51) — link pieza↔proyecto (metadata vs DomainType).

---

# ISSUES

### [ADR-007-001] Infinity: Embeddings de Visión (3er `--model-id`) — #49
Cerrado (21 oct). Modelo elegido: `jinaai/jina-clip-v2` (dual-encoder EVA02-L + Jina-XLM-R, 1024 dims, 89 idiomas) sirviendo `image_embed` offline (`--model-id /data/clip_model`, `trust_remote_code`). Kit self-contained horneado por `scripts/prepare_clip_kit.py`; pin `transformers==4.51.3` como suelo y techo (el remote code importa `clip_loss`, eliminado en 4.52); `hf-modules/` gitignored y regenerable por script. Latencia CPU ~4,3 s/imagen → el diseño de reconocimiento on-demand queda confirmado (la captura de cámara solo cuando el usuario pregunta; los segundos-CPU aterrizan en el lane asíncrono).

### [ADR-007-002] Colección Qdrant de Imagen — #50
Abierto. Colección separada con dimensión/espacio propios (1024, similitud coseno); inicialización idempotente; triage del BUG-001 (#34, inicialización de Qdrant) si bloquea la creación.
