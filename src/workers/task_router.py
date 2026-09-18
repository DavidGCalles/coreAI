
"""
Registry puro para enrutamiento de tareas del worker.

Este modulo implementa el patron Registry para evitar que las llamadas a MCP
inventen tipos de tareas inexistentes. La validacion se realiza en la puerta
de entrada usando Pydantic (src/schemas/tasks.py), y aqui centralizamos
todos los manejadores de tareas.
"""

import logging
from typing import Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from src.db.models import Task
from src.schemas.tasks import TaskType

logger = logging.getLogger(__name__)

# =====================================================================
# MANEJADORES INDIVIDUALES (Funciones asincronas independientes)
# =====================================================================

async def handle_extract_entities(task: Task, session: AsyncSession) -> None:
    """
    Manejador para tareas de extraccion de entidades.
    Asimila datos e inyecta en Postgres/Qdrant.
    
    TODO: Implementar logica real de asimilacion
    """
    logger.info("  [Worker] Extraccion de entidades para la tarea %s", task.id)


async def handle_init_session(task: Task, session: AsyncSession) -> None:
    """
    Manejador para inicializacion de contexto de sesion.
    Prepara el entorno para una nueva interaccion.
    
    TODO: Implementar logica real de inicializacion
    """
    logger.info("  [Worker] Inicializando contexto de sesion para la tarea %s", task.id)


async def handle_follow_up(task: Task, session: AsyncSession) -> None:
    """
    Manejador para seguimiento de interaccion.
    Respuesta al usuario tras una tarea anterior.
    
    TODO: Implementar logica real de follow-up
    """
    logger.info("  [Worker] Ejecutando follow_up para la tarea %s", task.id)


async def handle_dummy_test_task(task: Task, session: AsyncSession) -> None:
    """
    Manejador para tareas dummy (solo satisfacen la suite de tests).
    
    TODO: Implementar logica real de test
    """
    logger.info("  [Worker] Dummy test ejecutado con exito para la tarea %s", task.id)


# =====================================================================
# REGISTRO CENTRAL (Diccionario TASK_REGISTRY)
# =====================================================================

TASK_REGISTRY: Dict[TaskType, Any] = {
    TaskType.EXTRACT_ENTITIES: handle_extract_entities,
    TaskType.INIT_SESSION: handle_init_session,
    TaskType.FOLLOW_UP: handle_follow_up,
    TaskType.DUMMY_TEST_TASK: handle_dummy_test_task,
}


# =====================================================================
# ORQUESTADORA CENTRAL
# =====================================================================

async def route_task(task: Task, session: AsyncSession) -> None:
    """
    Enrutador central del worker.
    
    Extrae el valor bruto del tipo de tarea desde el JSON (task.payload.get("task_type")).
    Valida que existe en el catalogo TASK_REGISTRY. Si el tipo es desconocido o corrupto,
    levanta un ValueError. Si esta en el catalogo pero no tiene manejador asignado,
    levanta un NotImplementedError.
    Ejecuta el manejador pasandole la tarea y la sesion asincrona de base de datos.
    
    Args:
        task: Instancia de la tarea SQLAlchemy
        session: Sesion asincrona de Postgres
        
    Raises:
        ValueError: Si el tipo de tarea es desconocido o corrupto
        NotImplementedError: Si el tipo esta en el esquema pero carece de manejador
        Exception: Cualquier error durante la ejecucion del manejador
    """
    # Extraer el valor bruto del tipo de tarea desde el JSON
    raw_type = task.payload.get("task_type")
    
    if raw_type is None:
        raise ValueError(f"Tipo de tarea no proporcionado en task.payload para la tarea {task.id}")
    
    # Validar que existe en el catalogo TaskType (Pydantic/StrEnum)
    try:
        task_type = TaskType(raw_type)
    except ValueError as e:
        raise ValueError(f"Tipo de tarea desconocido, corrupto o fuera de catalogo: {raw_type}") from e
        
    # Recuperar el manejador desde el diccionario TASK_REGISTRY
    handler = TASK_REGISTRY.get(task_type)
    
    if handler is None:
        raise NotImplementedError(
            f"El tipo de tarea '{task_type.value}' esta en el esquema pero carece de manejador asignado."
        )
        
    # Ejecutar el manejador pasandole la tarea y la sesion asincrona
    await handler(task, session)
