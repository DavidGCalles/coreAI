import logging
from typing import Callable, Awaitable, Dict
from sqlalchemy.ext.asyncio import AsyncSession
from src.db.models import Task
from src.schemas.tasks import TaskType

logger = logging.getLogger(__name__)

# Firma estándar para cualquier manejador de tareas
TaskHandler = Callable[[Task, AsyncSession], Awaitable[None]]

# =====================================================================
# MANEJADORES INDIVIDUALES
# =====================================================================
async def handle_extract_entities(task: Task, session: AsyncSession) -> None:
    # Aquí irá la lógica real de asimilación e inyección en Postgres/Qdrant
    logger.info("  [Worker] Simulando extracción de entidades para la tarea %s", task.id)

async def handle_dummy(task: Task, session: AsyncSession) -> None:
    # Manejador vacío para satisfacer la suite de tests
    logger.info("  [Worker] Dummy test ejecutado con éxito para la tarea %s", task.id)

async def handle_init_session(task: Task, session: AsyncSession) -> None:
    logger.info("  [Worker] Inicializando contexto de sesión para la tarea %s", task.id)

# =====================================================================
# EL REGISTRO
# =====================================================================
TASK_REGISTRY: Dict[TaskType, TaskHandler] = {
    TaskType.EXTRACT_ENTITIES: handle_extract_entities,
    TaskType.DUMMY_TEST_TASK: handle_dummy,
    TaskType.INIT_SESSION: handle_init_session,
    TaskType.FOLLOW_UP: handle_dummy, # Reutilizamos el dummy para los tests
}

async def route_task(task: Task, session: AsyncSession) -> None:
    """
    Enrutador central. Extrae el tipo del JSONB, lo valida contra el Enum
    y dispara la función de negocio adecuada.
    """
    raw_type = task.payload.get("task_type")
    
    try:
        task_type = TaskType(raw_type)
    except ValueError:
        raise ValueError(f"Tipo de tarea desconocido, corrupto o fuera de catálogo: {raw_type}")
        
    handler = TASK_REGISTRY.get(task_type)
    
    if not handler:
        raise NotImplementedError(f"El tipo de tarea '{task_type.value}' está en el esquema pero carece de manejador ejecutable.")
        
    # Ejecutamos la lógica inyectando el estado de la tarea y la transacción activa
    await handler(task, session)