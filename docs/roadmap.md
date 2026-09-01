# Roadmap: [coreAI]

## Epic 1: [coreAI] Infraestructura Base y Orquestación
*   **Objetivo:** Aislamiento y despliegue del entorno base mediante `docker-compose`.
*   **Alcance:** Limpieza del compose, poda de Dockerfiles, aislamiento de redes (bridge estricto), persistencia de volúmenes en el host y depuración de dependencias (`requirements.txt`, `.env`).

## Epic 2: [coreAI] Capa de Memoria Unificada (Data Layer)
*   **Objetivo:** Persistencia determinista y semántica de estado sin fragmentación.
*   **Alcance:** PostgreSQL, SQLAlchemy, migraciones con Alembic, modelos de Entidades/Eventos con UUIDs primarios y vinculación estricta con la base de datos vectorial.

## Epic 3: [coreAI] Enrutamiento de Inferencia (LLM Proxy)
*   **Objetivo:** Abstracción del motor cognitivo con enfoque *local-first*.
*   **Alcance:** Contenedor LiteLLM, configuración de endpoints para embeddings e inferencia, y telemetría de consumo de tokens.

## Epic 4: [coreAI] Córtex Asíncrono (Event-Driven Workers)
*   **Objetivo:** Procesamiento en segundo plano y asimilación autónoma de contexto.
*   **Alcance:** Broker de mensajería (Redis/RabbitMQ), arquitectura de colas (Celery/RQ) y pipelines de consolidación nocturna de memoria.

## Epic 5: [coreAI] Gateway de Comunicación (Servidor MCP)
*   **Objetivo:** Exposición estandarizada y hermética del sistema hacia clientes externos.
*   **Alcance:** Servidor Model Context Protocol (MCP) y herramienta central `memory_tool` para interactuar con la memoria unificada.