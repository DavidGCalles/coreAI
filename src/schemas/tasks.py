from pydantic import BaseModel, Field
from typing import Any
from uuid import UUID
from enum import StrEnum

class TaskType(StrEnum):
    """Catálogo estricto de capacidades del Córtex Asíncrono."""
    EXTRACT_ENTITIES = "extract_entities"
    INIT_SESSION = "init_session"
    FOLLOW_UP = "follow_up"
    DUMMY_TEST_TASK = "dummy_test_task"

class TaskDispatchRequest(BaseModel):
    """
    Contrato estricto para la ingesta de tareas vía MCP.
    Fuerza al cliente a estructurar su caos antes de tocar la base de datos.
    """
    task_type: TaskType = Field(
        ..., 
        description="Identificador exacto del tipo de tarea. Solo se aceptan los valores listados."
    )
    task_payload: dict[str, Any] = Field(
        default_factory=dict, 
        description="Carga útil en formato JSON con los datos específicos de la tarea."
    )
    session_id: UUID | None = Field(
        None, 
        description="Opcional. UUID de la sesión si la tarea pertenece a un hilo existente."
    )
    entity_id: UUID | None = Field(
        None, 
        description="Opcional. UUID del dueño. Si se omite junto con session_id, el sistema auto-asignará uno."
    )

class TaskDispatchResponse(BaseModel):
    task_id: UUID
    session_id: UUID
    status: str = "PENDING"