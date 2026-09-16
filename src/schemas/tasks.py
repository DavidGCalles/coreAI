from pydantic import BaseModel, Field
from typing import Any
from uuid import UUID

class TaskDispatchRequest(BaseModel):
    """
    Contrato estricto para la ingesta de tareas vía MCP.
    Fuerza al cliente a estructurar su caos antes de tocar la base de datos.
    """
    task_type: str = Field(
        ..., 
        description="Identificador del tipo de tarea (ej. 'summarize_document', 'extract_entities')."
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
    """
    Contrato de salida.
    Vital para devolverle al cliente los UUIDs autogenerados durante la vivificación.
    """
    task_id: UUID
    session_id: UUID
    status: str = "PENDING"