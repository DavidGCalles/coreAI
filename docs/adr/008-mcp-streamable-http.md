# ADR-008: Superficie MCP sobre Streamable HTTP — El Substrato Sirve el Estándar del Protocolo

## Status
Proposed — aterrizado al arranque del [ADR-008-001] #76 (28 oct 2026); Proposed→Accepted con el [ADR-008-003] #78.

## Context and Problem Statement

MCP define transportes estándar: STDIO para procesos locales y streamable HTTP para acceso por red. coreAI sirve su frontera únicamente sobre STDIO — el cliente arranca el proceso y conversa por sus pipes. En el despliegue por compose eso solo funciona desde el host (`docker exec coreai_mcp ...`), el patrón que Cline y los IDE usan porque comparten máquina con el daemon de Docker.

No se está sirviendo el estándar del protocolo, y eso es deuda técnica que ya está pasando factura real. Un edge client contenerizado no puede hablar con la frontera: `docker exec` desde dentro de un contenedor exige montar `/var/run/docker.sock` — DinD de facto, agujero de seguridad vetado — así que todo cliente que viva en la red del compose queda fuera del sustrato por diseño. El primer caso ya existe: el capturador de elios-client (FR-1-003) tiene que orquestar desde el host o esperar. Y la factura crece por inquilino — no se paga una vez.

Mantener STDIO como única superficie no es neutra: convierte el acceso de clientes en plomería de cada inquilino, cuando el acceso por red a la frontera es responsabilidad del substrato. Pasa el test del banco: cualquier inquilino con un edge contenerizado necesita exactamente esta superficie.

**Pregunta:** ¿sobre qué transportes sirve coreAI su frontera MCP, y con qué papel queda STDIO?

## Decision Outcome

La frontera se sirve sobre **streamable HTTP** (transporte estándar de MCP) en la red interna del compose, como superficie de acceso de clientes por red. El mismo server sirve ambos transportes — mismas tools (`coreai_dispatch_task`, `coreai_get_task`, `coreai_search_context`, `coreai_search_image`), cero duplicación de lógica:

- **STDIO queda como vía interina documentada** — patrón IDE/host, intacto.
- Sin auth/TLS en esta iteración: la superficie vive en la red interna del compose — decisión explícita, no omisión.
- Streamable HTTP trae gestión de sesión propia (`Mcp-Session-Id`); la semántica por-sesión de la frontera no cambia.
- El puerto 8000 del compose es vestigial (CMD uvicorn muerto del Dockerfile): se reutiliza o se renombra — una sola fuente de verdad de puertos.
- Primera verificación del work item: el delta entre la surface v1 de `main.py` (`Server` + `stdio_server`) y el serving streamable HTTP del SDK python v2 (FastMCP).

### Positive Consequences
*   **Deuda saldada en el sitio correcto.** El substrato sirve el estándar del protocolo; los clientes hablan MCP nativo por red, sin docker exec ni docker.sock.
*   **El acceso de clientes es del substrato, no de los inquilinos.** Inquilino nuevo = edge nuevo sobre la misma superficie; cero plomería de transporte por inquilino.
*   **STDIO intacto.** Cline y el flujo IDE no cambian; la vía interina se documenta, no se elimina.

### Negative Consequences
*   **Superficie expuesta sin auth.** Cualquier proceso de la red interna del compose alcanza la frontera; el perímetro es la propia red, y así queda declarado.
*   **Coste de serving.** El delta surface v1 → SDK v2 puede tocar `main.py` más allá de añadir un transporte.
*   **Dos transportes vivos.** STDIO y HTTP coexisten y se testean; la deuda se cierra cuando el ecosistema retire STDIO, no antes.

## Related
*   ADR-004 — Tracer Bullet MCP.
*   ADR-005 (#36) — Doctrina de Ingesta Unificada.
*   ADR-006 (#40) — Contratos de Tarea MCP.
*   ADR-007 (#48) — Memoria Multimodal.

---

# ISSUES

### [ADR-008-001] Spike de serving — #76
En curso (28 oct). Verificación del delta entre la surface v1 de `main.py` (`Server` + `add_request_handler` + `PassthroughRequest` + `stdio_server`) y el serving streamable HTTP del SDK python v2: PoC en `scratch/` (mismo server, las 4 tools con handlers mock) hablado por STDIO y por streamable HTTP con cliente SDK; decisión de vía registrada en el issue con evidencia (tools/list idéntico por ambos transportes); este doc aterriza al repo con su arranque (regla de la casa).

### [ADR-008-002] Serving en main.py — #77
Abierto. La vía elegida por el spike aplicada a la frontera real: mismo server, dos transportes, cero duplicación de lógica de tools. Suite de la casa en ambos transportes (tools/list idéntico; round-trip HTTP initialize → tools/list → dispatch → poll → search) y smoke STDIO con el patrón Cline (`docker exec`) intacto.

### [ADR-008-003] Exposición en el compose y E2E real desde la red interna — #78
Abierto. Servicio en la red interna del compose (puerto único como fuente de verdad — reutilizar el 8000 vestigial o renombrar), E2E HTTP real desde un contenedor de la red (los mismos E2E que hoy corren por STDIO), Cline intacto, y este ADR Proposed→Accepted con la sección Post-implementación.
