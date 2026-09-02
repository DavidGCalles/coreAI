# [coreAI] Infraestructura: Purga del requirements.txt y .env

## Contexto
Los archivos `requirements.txt` y `.envExample` actuales son un coladero de la versión anterior. Deben ser reducidos a lo estrictamente necesario para levantar la arquitectura de persistencia y enrutamiento.

## Tareas
- [x] Vaciar el `requirements.txt` de cualquier librería reactiva, orquestadores de agentes de alto nivel o integraciones externas superfluas.
- [x] Fijar las versiones de las dependencias core: framework MCP, `SQLAlchemy`, `alembic`, `psycopg2-binary` y el framework del sistema de colas (Celery/RQ).
- [x] Limpiar `.envExample` de tokens muertos y actualizarlo con las rutas de conexión definitivas a Postgres, Redis y variables de configuración de LiteLLM.

## Criterios de Aceptación
- El comando `pip install -r requirements.txt` levanta el entorno virtual sin conflictos de dependencias ni avisos de seguridad críticos.
- Cualquier desarrollador puede levantar el entorno local copiando `.envExample` a `.env` sin necesidad de inyectar variables extra no documentadas.