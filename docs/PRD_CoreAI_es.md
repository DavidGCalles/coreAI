# Product Requirements Document (PRD): [coreAI] - Nivel Arquitectura Base

## 1. Naturaleza del Sistema (El Motor Core)
[coreAI] es un backend transaccional de cognición asíncrona y gestión de estado. Actúa como el sistema nervioso central (headless) para cualquier agente o cliente externo. Su función no es "hacer cosas", sino mantener la coherencia de la realidad (datos), procesar contexto en la sombra y exponer una interfaz estandarizada para que otros operen.

## 2. Subsistema de Memoria (Grafo Híbrido Relacional-Vectorial)
La memoria no es un simple almacén, es un motor de resolución de entidades y contexto temporal.

*   **Motor Relacional (PostgreSQL):** Custodio absoluto de la verdad. Define el esquema duro:
    *   *Registro de Entidades:* Nodos del sistema (personas, proyectos, conceptos, dispositivos de infraestructura).
    *   *Historial Episódico:* Log inmutable de todas las transacciones, interacciones y telemetría.
    *   *Control de Acceso (RBAC):* Autenticación y permisos a nivel de base de datos para los clientes/agentes que consuman la API.
*   **Motor Semántico (pgvector/Vector DB):** Vinculado mediante foreign keys (UUIDs) a las entidades relacionales. Permite la recuperación por similitud conceptual.
*   **Decaimiento y Compresión:** Lógica nativa de base de datos para comprimir eventos antiguos en "recuerdos consolidados", liberando espacio de contexto sin perder la trazabilidad.

## 3. Gateway de Protocolo (MCP Registry)
[coreAI] no hardcodea herramientas. Actúa como un *Registry* dinámico bajo el estándar Model Context Protocol (MCP).

*   **Registro Dinámico de Capacidades:** El sistema expone un endpoint donde los servicios externos (o módulos internos) registran sus capacidades (manifiestos). 
*   **Orquestación de Entradas/Salidas:** Ya sea controlar un hipervisor, auditar logs de una red local o leer sensores físicos, el cliente solo ve un servidor MCP unificado. [coreAI] valida los permisos y enruta la petición a la herramienta correspondiente.
*   **Aislamiento:** Si una herramienta falla, el núcleo transaccional de [coreAI] ni se entera.

## 4. Córtex Asíncrono (Event-Driven Background Processing)
El sistema no puede depender de que un cliente haga un *request* para pensar. Debe ser impulsado por eventos (Event Bus / Message Queue).

*   **Pipelines de Ingesta:** Cuando se inyecta nueva información (un log, una interacción), se encola un evento.
*   **Workers Desacoplados:** Consumidores asíncronos recogen los eventos para:
    *   Extraer nuevas entidades y actualizar el grafo relacional.
    *   Generar embeddings y volcarlos a la base vectorial.
    *   Auditar inconsistencias en los datos.
*   **Rutinas de Mantenimiento:** Tareas programadas pesadas para reorganización de índices y compresión nocturna.

## 5. Enrutamiento de Inferencia Soberano (LLM Proxy)
[coreAI] es agnóstico al motor cognitivo, pero prioriza la soberanía de los datos.

*   **Proxy Transparente (LiteLLM):** Todo tráfico de inferencia pasa por un embudo controlado.
*   **Balanceo Local-First:** Las tareas internas del *Córtex Asíncrono* (extraer entidades, resumir) se enrutan obligatoriamente al clúster de hardware local (modelos open-source). 
*   **Fallback Externo:** Solo si la carga computacional lo exige o el hardware local no está disponible, el proxy deriva la petición a APIs externas, dejando un rastro de auditoría y coste en la base de datos relacional.