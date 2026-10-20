from pydantic import BaseModel, Field
from typing import Any
from uuid import UUID
from enum import StrEnum
from datetime import datetime

class TaskType(StrEnum):
    """Catálogo estricto de capacidades del Córtex Asíncrono."""
    EXTRACT_ENTITIES = "extract_entities"
    INIT_SESSION = "init_session"
    FOLLOW_UP = "follow_up"
    DUMMY_TEST_TASK = "dummy_test_task"
    VECTORIZE_EVENT = "vectorize_event"
    CONSOLIDATE_MEMORY = "consolidate_memory"

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

class TaskGetRequest(BaseModel):
    """
    Contrato estricto para la consulta de estado de una tarea vía MCP (BUG-003, #53).
    Única clave de consulta: el task_id tal como lo devolvió coreai_dispatch_task.
    """
    task_id: UUID = Field(
        ...,
        description="UUID de la tarea, tal como lo devolvió coreai_dispatch_task."
    )

class TaskStatusResponse(BaseModel):
    """
    Vista de estado de una tarea (respuesta de coreai_get_task, BUG-003 #53).
    Perfil mínimo deliberado: solo lo que un agente necesita para cerrar el
    bucle write-then-read. El payload crudo JAMÁS viaja en la respuesta —
    reduce el perfil de la información que cruza la frontera MCP.
    """
    task_id: UUID
    session_id: UUID
    task_type: str | None = Field(
        None,
        description="Tipo de la tarea, extraído del JSONB payload. None si el payload está corrupto."
    )
    status: str = Field(
        ...,
        description="PENDING | RUNNING | COMPLETED | FAILED."
    )
    error_trace: str | None = Field(
        None,
        description="Traza de error si la tarea falló en ejecución (fallo legítimo asíncrono, no de contrato). None en cualquier otro caso."
    )
    created_at: datetime
    updated_at: datetime
