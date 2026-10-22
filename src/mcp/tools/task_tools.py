import json
import logging
from mcp.types import Tool, CallToolRequestParams
from pydantic import ValidationError

from src.db.database import AsyncSessionLocal
from src.repositories.relational import TaskRepository, SessionRepository, EntityRepository
from src.managers.task_manager import TaskManager
from src.schemas.tasks import TaskDispatchRequest, TaskGetRequest, TaskStatusResponse
from src.contracts import task_contract_registry, contract_uri, CONTRACT_URI_TEMPLATE

logger = logging.getLogger(__name__)

DISPATCH_TOOL_DESCRIPTION = (
    "Única apertura de ingesta del Córtex Asíncrono: toda escritura "
    "mutante del sistema entra como tarea asíncrona por esta tool. Semántica "
    "write-then-read: el dispatch devuelve PENDING con task_id y la consistencia "
    "es eventual — consulta el estado por task_id → done, NUNCA lo infier de una "
    "búsqueda en memoria. El poll está ACOTADO: uno o dos reintentos con "
    "coreai_get_task y sigues; NO bloques el bucle esperando el COMPLETED. Cada "
    "task_type tiene un contrato de payload que se valida aquí de forma "
    "síncrona: si el payload viola el contrato, la llamada falla con isError y "
    "el contrato inline — clase de fallo NO reintentable (no esperes que "
    "reintentar lo arregle; corrige el payload). Catálogo de contratos: "
    + CONTRACT_URI_TEMPLATE
)

GET_TASK_TOOL_DESCRIPTION = (
    "Consulta de estado de una tarea del Córtex Asíncrono (read-only, "
    "cero mutación). Cierra el bucle write-then-read: coreai_dispatch_task "
    "devuelve PENDING con un task_id; consulta aquí el estado real antes de "
    "dar nada por hecho. COMPLETED → done, no lo reinterpretes. FAILED → la "
    "tarea legítima falló en ejecución (handler, infraestructura): lee "
    "error_trace para diagnosticar — es un fallo del sistema, no de tu "
    "payload. PENDING/RUNNING → la consistencia es eventual: ACOTA el poll a "
    "uno o dos reintentos y sigue con otra cosa, no bloquees al cliente "
    "esperando el estado; NUNCA infieras el resultado de una búsqueda en "
    "memoria."
)

def get_task_tools() -> list[Tool]:
    """Devuelve la lista de herramientas de tareas para el registro central."""
    return [
        Tool(
            name="coreai_dispatch_task",
            description=DISPATCH_TOOL_DESCRIPTION,
            inputSchema=TaskDispatchRequest.model_json_schema()
        ),
        Tool(
            name="coreai_get_task",
            description=GET_TASK_TOOL_DESCRIPTION,
            inputSchema=TaskGetRequest.model_json_schema()
        )
    ]

def _format_contract_violation(task_type, error: ValidationError) -> str:
    """
    Error autónomo de la frontera: violaciones formateadas + URI del contrato +
    esquema compacta inline. Debe bastar para que un agente se autocorriga
    en-loop sin leer código fuente.
    """
    violations = []
    for err in error.errors(include_url=False):
        loc = ".".join(str(part) for part in err["loc"]) or "(raíz)"
        violations.append(f"  - {loc}: {err['msg']} (tipo: {err['type']})")
    schema_inline = json.dumps(
        task_contract_registry.schema(task_type),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return (
        f"El payload viola el contrato de '{task_type}'. "
        f"La tarea NO se encoló: cero filas nuevas en tasks y ninguna "
        f"sesión/entidad auto-vivificada para este intento."
        f"Violaciones:"+ "".join(violations)
        + f"Contrato del tipo: {contract_uri(task_type)}. "
        f"Esquema del contrato: {schema_inline}"
        f"Corrige el task_payload para cumplir la esquema y reintenta el dispatch."
    )

async def handle_task_tool(name: str, arguments: dict) -> dict:
    """Manejador específico para las herramientas de tareas."""

    # =================================================================
    # coreai_get_task (BUG-003, #53): consulta read-only del estado de
    # una tarea. Cierra el bucle write-then-read abierto por el dispatch.
    # =================================================================
    if name == "coreai_get_task":
        try:
            request_data = TaskGetRequest.model_validate(arguments)
        except Exception as e:
            logger.error("Argumentos malformados en get_task: %s", e)
            return {"content": [{"type": "text", "text": f"Error de validación: {str(e)}"}], "isError": True}

        try:
            async with AsyncSessionLocal() as session:
                task_repo = TaskRepository(session)
                task = await task_repo.get(request_data.task_id)

                if task is None:
                    logger.warning("Consulta de tarea inexistente: %s", request_data.task_id)
                    return {
                        "content": [{
                            "type": "text",
                            "text": (
                                f"La tarea {request_data.task_id} no existe. "
                                "No hay fila con ese task_id: verifica que el UUID "
                                "venga de la respuesta de coreai_dispatch_task."
                            )
                        }],
                        "isError": True
                    }

                # task_type y error_trace viven dentro del JSONB payload
                # (los inyectan TaskManager y fail_task): extracción defensiva.
                payload = task.payload if isinstance(task.payload, dict) else {}
                status = task.status.value if hasattr(task.status, "value") else str(task.status)

                response = TaskStatusResponse(
                    task_id=task.id,
                    session_id=task.session_id,
                    task_type=payload.get("task_type"),
                    status=status,
                    error_trace=payload.get("error_trace"),
                    created_at=task.created_at,
                    updated_at=task.updated_at,
                )
                # Read-only estricto: solo un SELECT cruzó la frontera; la
                # sesión efímera se cierra sin commit ni flush de escritura.
                return {
                    "content": [{"type": "text", "text": response.model_dump_json(indent=2)}],
                    "isError": False
                }
        except Exception as e:
            logger.error("Fallo interno en la consulta de tarea: %s", e)
            return {"content": [{"type": "text", "text": f"Fallo estructural: {str(e)}"}], "isError": True}

    if name == "coreai_dispatch_task":
        try:
            # 1. Validación estricta en el borde (Pydantic hace de escudo)
            request_data = TaskDispatchRequest.model_validate(arguments)
        except Exception as e:
            logger.error("Payload malformado en dispatch_task: %s", e)
            return {"content": [{"type": "text", "text": f"Error de validación: {str(e)}"}], "isError": True}

        # 1.5 Frontera de contratos (ADR-006-001, #43): sin contrato no hay despacho.
        #     Un tipo sin contrato NO pasa como dict libre: fallo explícito en la tool call.
        if not task_contract_registry.has_contract(request_data.task_type):
            contracted = ", ".join(task_contract_registry.list()) or "ninguno"
            message = (
                f"El tipo de tarea '{request_data.task_type}' no tiene contrato registrado. "
                f"Sin contrato no hay despacho. "
                f"Tipos con contrato: {contracted}."
            )
            logger.warning(
                "Dispatch rechazado por falta de contrato: %s", request_data.task_type
            )
            return {"content": [{"type": "text", "text": message}], "isError": True}

        # 1.6 Frontera de FORMA (ADR-006-003, #44): el task_payload se valida contra
        #     el contrato del tipo DENTRO de la tool call y ANTES de la sesión efímera
        #     — una violación jamás llega a la cola ni auto-vivifica sesión/entidad
        #     (el patrón BUG-002 muere aquí: síncrono y visible para el agente).
        #     El loader del contrato normaliza: el motor solo recibe payloads canónicos.
        try:
            request_data.task_payload = task_contract_registry.validate(
                request_data.task_type, request_data.task_payload
            )
        except ValidationError as e:
            logger.warning(
                "Dispatch rechazado por violación de contrato: %s", request_data.task_type
            )
            return {"content": [{"type": "text", "text": _format_contract_violation(request_data.task_type, e)}], "isError": True}

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
