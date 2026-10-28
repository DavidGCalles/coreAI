# ADR-008: Superficie MCP sobre Streamable HTTP — El Substrato Sirve el Estándar del Protocolo

## Status
Accepted (28 oct 2026) — familia [ADR-008] completada: spike de vía [ADR-008-001] #76, serving en la frontera [ADR-008-002] #77 (PR #81), exposición y verificación [ADR-008-003] #78. STDIO queda como vía interina documentada.

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

## Post-implementación (28 oct 2026)

- **Vía aterrizada:** dual-transport con la surface actual — el propio `Server` del SDK (mcp 2.1.1) monta el Starlette (`streamable_http_app()`) sobre la MISMA instancia de `main.py`. FastMCP descartada (validación estricta de parámetros incompatible con el patrón `PassthroughRequest` del post-mortem del ADR-004); ASGI a mano descartado (duplicaba el session manager). Evidencia: [ADR-008-001] #76.
- **Serving:** `--serving stdio|http|dual` (default `stdio`). El service del compose despliega en `http` directo (0.0.0.0:8000): STDIO no vive en el service — cada IDE sigue spawneando su proceso por `docker exec -i` (patrón Cline intacto, verificado en vivo: los dos servers de Cline en verde).
- **Desviación registrada sobre el alcance original:** el issue pedía "sin exposición a host/LAN por defecto"; el 8000 queda publicado al host (`8000:8000`) — el host es cliente legítimo: el sistema funciona y eso justifica el cambio, no al revés. El perímetro declarado: red interna del compose + host de desarrollo; la LAN queda sin acceso explícito.
- **Verificación:** suite de ambos transportes verde (tools/list idéntico; round-trip HTTP initialize → dispatch → poll → search; router estricto). E2E real desde la red interna registrado en #78.

---

# ISSUES

### [ADR-008-001] Spike de serving — #76
Completado (28 oct). PoC en `scratch/` con el mismo `Server` hablado por STDIO y por streamable HTTP: tools/list idéntico (4/4) por ambos transportes. Vía decidida con evidencia en el issue: dual-transport con la surface actual (`Server.streamable_http_app()` + uvicorn); FastMCP y ASGI a mano descartadas. Este doc aterrizó al repo con su arranque (regla de la casa).

### [ADR-008-002] Serving en main.py — #77
Completado (28 oct, PR #81). `--serving stdio|http|dual` (default `stdio`) sobre la MISMA instancia, cero duplicación de lógica. Suite de ambos transportes verde (tools/list idéntico; round-trip HTTP initialize → tools/list → dispatch → poll → search; router estricto) y smoke STDIO del patrón Cline intacto; el service del compose despliega en `http` directo.

### [ADR-008-003] Exposición en el compose y E2E real desde la red interna — #78
Completado (28 oct). El 8000 reutilizado como única fuente de verdad de puertos y publicado al host (cliente legítimo: Cline por streamable HTTP verificado en vivo); E2E real desde un contenedor de la red (`scratch/e2e_http_red_interna.py`, resultado en el issue); Cline intacto (dos servers en verde); este ADR Accepted con Post-implementación.
