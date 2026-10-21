# ADR-005: Doctrina de Ingesta Unificada — Escrituras vía Event Lane y Ruta de Lectura Síncrona Acotada

## Status
Proposed

## Context and Problem Statement
coreAI expone actualmente dos superficies de ingesta divergentes: (1) el córtex asíncrono (Epic 4) — `coreai_dispatch_task` → tabla `tasks` → motor de workers (`SELECT ... FOR UPDATE SKIP LOCKED`), con semánticas de fallo explícitas (retries, Exponential Backoff, DLQ — Issue 4.4) y observabilidad centralizada (ADR-003); y (2) una superficie síncrona de memoria en `main` (`coreai_store_context` / `coreai_search_context`) que permite a cualquier cliente MCP mutar y consultar la capa de memoria por fuera del sistema de tareas.

La línea del Epic 4 aún no incluye `vector_tools.py`. Antes de que la superficie de memoria aterrice en esta línea, el contrato de ingesta debe quedar resuelto; de lo contrario, el propio merge decidirá la doctrina en silencio.

El problema es asimétrico: las escrituras vía tool síncrona violan la doctrina del sustrato — sin política de reintentos, sin dead-lettering, sin backpressure, e invisibles para las rutinas de consolidación, decaimiento y purga de huérfanos que razonan sobre el flujo de tareas (y para la traza Loki/LLMAudit). Las lecturas vía event lane son la herramienta equivocada: el recall estilo identificación es fundamentalmente request-response y necesita respuestas deterministas a escala de milisegundos — enrutarlo por dispatch más correlación y polling añadiría maquinaria al camino más caliente del sistema.

**Pregunta:** ¿cuál es el contrato de ingesta único y agnóstico de cliente para todo input externo?

## Decision Outcome
Toda ingesta mutante de estado entra exclusivamente por `coreai_dispatch_task`. La ingesta de memoria se convierte en una tarea de la clase `VECTORIZE_EVENT`, ejecutada por su handler (Issue 4.3); los handlers son los únicos escritores de la capa de memoria. La superficie síncrona de escritura (`coreai_store_context`) **no se porta** a la línea del córtex.

Una única tool síncrona de recall (`coreai_search_context`, estrictamente read-only) permanece como la **única** apertura síncrona del sistema. El contrato write-then-read es eventualmente consistente por diseño: la completitud se consulta en la tarea (`task_id` → `done`), nunca se infiere de los resultados de búsqueda.

### Positive Consequences
*   **Única lane de escritura auditada.** Toda mutación tiene un UUID de tarea, semánticas de retry/DLQ y traza Loki/LLMAudit — una sola auditoría, sin excepciones.
*   **Rutinas de fondo coherentes.** Consolidación, decaimiento y purga de huérfanos observan el flujo completo de escrituras.
*   **Path de lectura magro.** El recall conserva la latencia request-response; sin maquinaria de correlación ni polling.
*   **Sustrato agnóstico de cliente.** Los nuevos productores llegan como nuevos tipos de tarea más handlers, nunca como nuevas superficies de tools síncronas.

### Negative Consequences
*   **Visibilidad eventual de escrituras.** Cambio de comportamiento para los consumidores de las tools síncronas.
*   **Esfuerzo de migración.** Los clientes existentes de `coreai_store_context` en `main` deben migrar a dispatch, con ventana de deprecación y cutover explícitos.
*   **Tentación permanente.** El acceso síncrono de la tool de recall invita al feature creep del lado de escritura; el límite read-only se defiende por test y revisión, no se asume.

## Related
*   ADR-002, ADR-003, ADR-004.
*   ADR-006 (#40) — Contratos de Tarea MCP (refina la frontera: forma en el dispatch, semántica en el handler).
*   ADR-007 (#48) — Memoria Multimodal.

---

# ISSUES

### [ADR-005-001] Handler de la Event Lane de Ingesta — #38
Cerrado por absorción: el handler de `VECTORIZE_EVENT` validado E2E dos veces (evento de diagnóstico del 18 oct, smoke del 20 oct); su item de validación en frontera quedó superado y reforzado por ADR-006 (validación de forma en el dispatch).

### [ADR-005-002] Endurecimiento del Recall: `coreai_search_context` estrictamente read-only — #37
Abierto. Reescritura como código fresco — sin portar el legado de `vector_tools.py` (su eliminación se aceptó as-is en el merge Epic 4 → main). DoD: tras aterrizar, `tools/list` = `coreai_dispatch_task` + `coreai_get_task` + `coreai_search_context`.

### [ADR-005-003] Documentación de Contratos y Ventana de Deprecación — #39
Cerrado por absorción: las descripciones de tools ya declaran el contrato write-then-read (dispatch desde ADR-006-003, `coreai_get_task` desde BUG-003 #53); la publicación machine-readable queda absorbida por [ADR-006-002] #42; la ventana de deprecación se descartó como obsoleta — `coreai_store_context` muere con la eliminación aceptada de `vector_tools.py`, sin nada que migrar.
