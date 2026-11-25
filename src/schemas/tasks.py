from pydantic import BaseModel, ConfigDict, Field
from typing import Any
from uuid import UUID
from enum import StrEnum
from datetime import datetime

from src.db.entities_config import ID_PATTERN, MAX_ID_LENGTH

class TaskType(StrEnum):
    """Catálogo estricto de capacidades del Córtex Asíncrono."""
    EXTRACT_ENTITIES = "extract_entities"
    INIT_SESSION = "init_session"
    FOLLOW_UP = "follow_up"
    DUMMY_TEST_TASK = "dummy_test_task"
    VECTORIZE_EVENT = "vectorize_event"
    CONSOLIDATE_MEMORY = "consolidate_memory"
    VECTORIZE_IMAGE_EVENT = "vectorize_image_event"
    AMEND_MEMORY_POINT = "amend_memory_point"

class TaskDispatchRequest(BaseModel):
    """
    Contrato estricto para la ingesta de tareas vía MCP.
    Fuerza al cliente a estructurar su caos antes de tocar la base de datos.

    Sobre plano y cerrado (ADR-012-002, #113): las claves desconocidas
    rebotan. El sobre del despacho es del sustrato, y el `entity_id` UUID
    legacy (muerto con la auto-vivificación) no puede colar silencioso: un
    remitente estancado se entera AQUÍ, no cuando su tarea aparece
    titulada a otro.
    """
    model_config = ConfigDict(extra="forbid")

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
    entity: str | None = Field(
        None,
        pattern=ID_PATTERN.pattern,
        max_length=MAX_ID_LENGTH,
        description=(
            "Opcional. Slug de la identidad que despacha (ADR-012-002), tal "
            "como vive registrada en el despliegue (YAML + bootstrap, "
            "ADR-012-001). Declarada y registrada: la respuesta devuelve su "
            "entity_id resuelta. Declarada y NO registrada: fallo "
            "determinista con el catálogo de entidades válidas, la tarea no "
            "se crea y reintentar no sirve. Ausente: system_default, "
            "explícito en la respuesta."
        ),
    )

class TaskDispatchResponse(BaseModel):
    """
    Desenlace del dispatch: la tarea en PENDING y la identidad YA resuelta.
    [ADR-012-002] #113: la entity_id viaja SIEMPRE — también cuando el
    despacho no declaró identidad (system_default explícito): la resolución
    deja de ser silenciosa.
    """
    task_id: UUID
    session_id: UUID
    entity_id: UUID = Field(
        ...,
        description=(
            "Entidad que la frontera resolvió contra el registro del "
            "despliegue: la titularidad de la fila y de la sesión huérfana "
            "si se vivificó."
        ),
    )
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

class TaskStatusFilter(StrEnum):
    """
    Estados de tarea consultables desde la frontera MCP (4.4-003, #105).
    Espejo deliberado del TaskStatus de la BD (src/db/models.py): la frontera
    valida forma sin acoplarse a la capa de datos.
    """
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"

class TaskListRequest(BaseModel):
    """
    Contrato estricto para el listado de tareas por estado vía MCP (4.4-003,
    #105). La DLQ lógica del Córtex es el estado FAILED: tareas legítimas que
    agotaron sus reintentos automáticos.
    """
    status: TaskStatusFilter = Field(
        ...,
        description=(
            "Estado exacto a listar: PENDING | RUNNING | COMPLETED | FAILED. "
            "La DLQ es FAILED."
        ),
    )
    limit: int = Field(
        50,
        ge=1,
        le=200,
        description=(
            "Máximo de tareas a devolver (default 50, techo 200). "
            "Orden FIFO: la más antigua primero."
        ),
    )

class TaskListItem(BaseModel):
    """
    Ítem del listado por estado (respuesta de coreai_list_tasks, 4.4-003 #105).
    Perfil de DLQ: retry_count, error_history y edad junto al estado — lo que
    un agente necesita para decidir si una FAILED es reinyectable. El payload
    crudo JAMÁS viaja en la respuesta (perfil mínimo de la frontera).
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
    retry_count: int = Field(
        ...,
        description=(
            "Reintentos automáticos consumidos (4.4-001). 0 al nacer y 0 tras "
            "una reinyección manual: la mecánica se resetea con la tarea."
        ),
    )
    error_trace: str | None = Field(
        None,
        description="Última traza si la tarea falló en ejecución. None en cualquier otro caso."
    )
    error_history: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Historial append-only de intentos fallidos (4.4-002): entradas "
            "{attempt, at, trace}. La auditoría de la DLQ — intacta incluso "
            "tras una reinyección manual."
        ),
    )
    created_at: datetime
    updated_at: datetime
    age_seconds: int = Field(
        ...,
        description=(
            "Edad de la tarea en segundos desde su creación, calculada por el "
            "reloj de Postgres — el mismo que firma available_at."
        ),
    )

class TaskListResponse(BaseModel):
    """
    Respuesta de coreai_list_tasks (4.4-003 #105): el estado consultado, el
    conteo devuelto y los ítems en orden FIFO.
    """
    status: str
    count: int
    tasks: list[TaskListItem]

class TaskRetryRequest(BaseModel):
    """
    Contrato estricto para la reinyección manual de una FAILED vía MCP
    (4.4-003, #105). Única clave: el task_id tal como lo expone el listado
    de la DLQ o coreai_get_task.
    """
    task_id: UUID = Field(
        ...,
        description=(
            "UUID de la tarea FAILED a reinyectar, tal como lo devolvió "
            "coreai_list_tasks o coreai_get_task."
        ),
    )

class TaskRetryResponse(BaseModel):
    """
    Desenlace de la reinyección manual (respuesta de coreai_retry_failed_task,
    4.4-003 #105): la tarea vuelve a PENDING desde cero.
    """
    task_id: UUID
    session_id: UUID
    status: str = "PENDING"
    retry_count: int = 0
    available_at: datetime | None = Field(
        None,
        description="NULL por construcción: una reinyección es elegible YA para el claim del worker.",
    )
