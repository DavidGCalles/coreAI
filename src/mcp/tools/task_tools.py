import logging
from mcp.types import Tool, CallToolRequestParams

from src.db.database import AsyncSessionLocal
from src.repositories.relational import TaskRepository, SessionRepository, EntityRepository
from src.managers.task_manager import TaskManager
from src.schemas.tasks import TaskDispatchRequest

logger = logging.getLogger(__name__)

def get_task_tools() -> list[Tool]:
    """Devuelve la lista de herramientas de tareas para el registro central."""
    return [
        Tool(
            name="coreai_dispatch_task",
            description="Inyecta una nueva tarea asíncrona en el sistema. Soporta auto-vivificación de sesiones si no se proporciona un session_id explícito.",
            inputSchema=TaskDispatchRequest.model_json_schema()
        )
    ]

async def handle_task_tool(name: str, arguments: dict) -> dict:
    """Manejador específico para las herramientas de tareas."""
    if name == "coreai_dispatch_task":
        try:
            # 1. Validación estricta en el borde (Pydantic hace de escudo)
            request_data = TaskDispatchRequest.model_validate(arguments)
        except Exception as e:
            logger.error("Payload malformado en dispatch_task: %s", e)
            return {"content": [{"type": "text", "text": f"Error de validación: {str(e)}"}], "isError": True}

        # 2. Inyección transaccional con sesión efímera
        try:
            async with AsyncSessionLocal() as session:
                task_repo = TaskRepository(session)
                session_repo = SessionRepository(session)
                entity_repo = EntityRepository(session)
                
                manager = TaskManager(session, task_repo, session_repo, entity_repo)
                response = await manager.dispatch_task(request_data)
                
                # Devolvemos el contrato de salida serializado. Vital para que el cliente lea el session_id autogenerado.
                return {
                    "content": [{"type": "text", "text": response.model_dump_json(indent=2)}],
                    "isError": False
                }
        except Exception as e:
            logger.error("Fallo interno del motor de tareas: %s", e)
            return {"content": [{"type": "text", "text": f"Fallo estructural: {str(e)}"}], "isError": True}
    
    raise ValueError(f"Herramienta {name} no es gestionada por task_tools.")