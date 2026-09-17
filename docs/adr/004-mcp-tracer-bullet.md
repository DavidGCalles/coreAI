# ADR-004: Implementación de Bala Trazadora (Tracer Bullet) vía MCP para Validación I/O

## Status
Accepted

## Context and Problem Statement
El Epic 4 define la creación de un Córtex Asíncrono basado en PostgreSQL (`SELECT ... FOR UPDATE SKIP LOCKED`). Sin embargo, construir toda la arquitectura de consumo (workers, demonios, enrutadores) sin validar empíricamente la capa de ingesta genera un riesgo crítico de aislamiento teórico. Depender exclusivamente de tests de integración mediante *fixtures* oculta posibles fricciones en los contratos de datos reales. ¿Cómo garantizamos que el sistema es capaz de recibir, validar y persistir tareas externas antes de programar la lógica profunda que las consume?

## Decision Outcome
Se implementará una *Tracer Bullet* (Bala Trazadora) a nivel de I/O. Se desarrollará una herramienta nativa bajo el estándar Model Context Protocol (MCP) que actúe como el primer y único vector de ingesta para la tabla `tasks`. Esta decisión obliga a definir el contrato de datos (JSON Schema) por adelantado y permite validar el flujo *End-to-End* (desde el cliente externo hasta la base de datos relacional) antes de escribir una sola línea del demonio asíncrono.

### Positive Consequences
*   **Validación Temprana:** Confirma la operatividad del `TaskRepository` y la integridad transaccional de PostgreSQL en un entorno vivo.
*   **Contratos Estrictos:** Fuerza la definición de los esquemas de entrada, erradicando el *impedance mismatch* posterior entre la API y el motor de colas.
*   **Agencia Inmediata:** Permite a clientes externos (y al propio desarrollador a través del IDE) empezar a encolar tareas en el entorno de desarrollo instantáneamente.

### Negative Consequences
*   Requiere alterar el orden de ejecución previsto, extrayendo una tarea conceptual del Epic 5 (Gateway MCP) para inyectarla como bloqueador inicial del Epic 4.

---

# ISSUES

### [ADR-004-001] Gateway MCP: Endpoint de Ingesta de Tareas (Tracer Bullet)
**Descripción:**
Implementar y exponer la herramienta `coreai_dispatch_task` en el servidor MCP. Esta herramienta debe recibir un tipo de tarea (`task_type`) y su carga útil (`payload`), validarlos mediante un esquema estricto (Pydantic) y persistirlos en la base de datos utilizando la capa de repositorios, asignando por defecto el estado `PENDING`.

**DoD (Definition of Done):**
*   La herramienta `coreai_dispatch_task` está registrada correctamente y su manifiesto es visible en el inspector del cliente MCP.
*   El endpoint acepta peticiones, inyecta el registro en la tabla `tasks` de PostgreSQL y devuelve el UUID generado al cliente externo.
*   El esquema de entrada intercepta y rechaza *payloads* malformados antes de inicializar la transacción de base de datos.

**Tests / Validación:**
*   Implementar test de integración que simule una llamada `call_tool` del protocolo MCP hacia la herramienta `coreai_dispatch_task`.
*   Asertar que la respuesta MCP devuelve un `isError: False` y el bloque de texto contiene el UUID.
*   Asertar mediante consulta cruda a la base de datos que existe un registro con dicho UUID y que su estado es inmutablemente `PENDING`.

# Post-Mortem Técnico: ADR-004 - Implementación de Bala Trazadora (Tracer Bullet) vía MCP

## Resumen Ejecutivo
La ejecución del **ADR-004** validó con éxito la pasarela de entrada (I/O) hacia el motor transaccional de `coreAI` antes de desarrollar el Córtex Asíncrono. Sin embargo, la fricción no ocurrió en la capa de persistencia (PostgreSQL), que operó de manera determinista, sino en el enrutamiento opaco del SDK oficial de Model Context Protocol (v2.1.1) . Se implementó un patrón de "Caballo de Troya" en la capa de red para sortear la validación prematura del framework, delegando el control estricto de esquemas exclusivamente a nuestra lógica de negocio .

## 1. Decisiones Arquitectónicas Clave

### A. Patrón "Caballo de Troya" (PassthroughRequest)
El SDK de MCP interceptaba las peticiones `tools/call` y aplicaba una validación estricta de Pydantic sobre el sobre JSON-RPC completo . Cuando clientes externos (como el Inspector o Cline) inyectaban metadatos dinámicos propios del protocolo, el enrutador rechazaba la petición con un error opaco (`-32602 Invalid request parameters`), colapsando la transacción antes de rozar la lógica de la aplicación. 
*   **Resolución:** Se inyectó el modelo `PassthroughRequest` configurado con `ConfigDict(extra='allow')` . Esto neutraliza el cortocircuito del SDK, permitiendo que el servidor absorba la entropía del protocolo sin fallar, para extraer manualmente los campos `name` y `arguments` .

### B. Desplazamiento de la Frontera de Validación
Al silenciar la validación estricta del enrutador de MCP, la responsabilidad del contrato de datos se desplazó un nivel hacia abajo. 
*   **Implementación:** La validación dura se ejecuta ahora directamente en `handle_task_tool` mediante `TaskDispatchRequest.model_validate(arguments)` . Si el cliente envía un *payload* malformado, Pydantic intercepta el error en nuestro terreno y lo devuelve formateado como una respuesta MCP válida (`isError=True`), en lugar de desconectar abruptamente al cliente .

### C. Auto-vivificación Estructural
Se validó en producción la capacidad del `TaskManager` para gestionar el caos de clientes LLM autónomos . Cuando el cliente inyectó un `task_type` con su `task_payload` pero sin `session_id`, el orquestador generó dinámicamente la entidad por defecto y la sesión vinculada al vuelo . Esto garantiza la integridad referencial en PostgreSQL sin exigir que el cliente externo mantenga estados complejos.

## 2. Lecciones Aprendidas e Incidencias Técnicas

### A. La Trampa de los Tipos Nativos en el Router
Durante la depuración del error `-32602`, se intentó forzar al router a aceptar un tipo de dato genérico (`dict`) pasándolo como segundo argumento a `mcp_server.add_request_handler` .
*   **Impacto:** El SDK exige por diseño que el objeto inyectado posea el método `.model_validate()`. Inyectar un `dict` nativo provocó un *crash* total del servidor (`AttributeError`). El framework sacrifica la flexibilidad en favor del acoplamiento a Pydantic.

### B. El Espejismo de `tools/list`
La discrepancia entre el éxito de la petición `tools/list` y el colapso de `tools/call` generó un falso negativo arquitectónico . `tools/list` carece de argumentos y metadatos dinámicos, superando la validación estricta por omisión. Esto demostró que no se puede certificar la salud de la pasarela MCP basándose únicamente en el *handshake* inicial de las herramientas.

### C. Secuestro de Procesos (Zombis STDIO)
La depuración se vio gravemente obstaculizada por la naturaleza *stateful* del transporte STDIO . Clientes persistentes como Cline mantienen el proceso de Python vivo a través de la tubería estándar . 
*   **Impacto:** Las modificaciones en disco (como el parche del `PassthroughRequest`) no se reflejaban en tiempo real porque el proceso antiguo seguía enrutando en memoria . Requirió matar explícitamente el hilo del cliente MCP y reiniciar la ventana de comandos para aplicar los cambios del motor.