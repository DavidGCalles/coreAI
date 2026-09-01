# MoSCoW: [coreAI] - Versión Fundacional (V1.0)

## MUST HAVE
*   **Infraestructura Base:** Orquestación con `docker-compose` para aislar servicios (API, DBs, Proxy, Workers, Broker de mensajería).
*   **Motor Relacional Base:** PostgreSQL configurado con migraciones (Alembic) y SQLAlchemy. Esquemas iniciales para Entidades y Eventos usando UUIDs primarios.
*   **Motor Semántico Base:** Base de datos vectorial desplegada y operativa, con sincronización estricta por Foreign Key (UUID) hacia PostgreSQL.
*   **Gateway MCP:** Servidor principal exponiendo el estándar Model Context Protocol.
*   **Córtex Asíncrono (Workers):** Infraestructura de colas (ej. Celery/RQ + Redis) levantada y consumiendo eventos. El procesamiento en segundo plano es fundacional.
*   **Herramienta Núcleo (memory_tool):** Endpoint MCP funcional para ejecutar operaciones CRUD sobre PostgreSQL y consultas de similitud sobre la base vectorial.
*   **Enrutamiento Básico:** Contenedor LiteLLM configurado estáticamente para enrutar las peticiones de embeddings e inferencia necesarias.

## SHOULD HAVE
*   **Rutina de Consolidación (Cron):** Job asíncrono inicial de madrugada para leer eventos, resumirlos y re-inyectarlos como contexto consolidado.
*   **Telemetría de Costes:** Registro transaccional del consumo de tokens que LiteLLM devuelve en cada llamada.

## COULD HAVE
*   **Registro Dinámico de Herramientas:** Sistema para inyectar manifiestos de nuevos servidores MCP en caliente sin reinicios.
*   **Control de Acceso (RBAC):** Gestión de roles y permisos a nivel de base de datos.
*   **Decaimiento Avanzado de Memoria:** Algoritmos para archivar lógicamente recuerdos viejos según su curva de retención.

## WON'T HAVE
*   **Interfaces (UI/UX), clientes de mensajería o frontends de cualquier tipo.**
*   **Lógicas de orquestación de Agentes o modelos de "Swarm".**
*   **Memoria efímera de sesión orientada a flujos de conversación.**
*   **Herramientas MCP de prueba o utilidades genéricas ajenas a la persistencia.**