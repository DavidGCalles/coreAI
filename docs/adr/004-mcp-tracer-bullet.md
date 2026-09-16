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