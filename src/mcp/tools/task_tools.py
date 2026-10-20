import json
import logging
from mcp.types import Tool, CallToolRequestParams
from pydantic import ValidationError

from src.db.database import AsyncSessionLocal
from src.repositories.relational import TaskRepository, SessionRepository, EntityRepository
from src.managers.task_manager import TaskManager
from src.schemas.tasks import TaskDispatchRequest
from src.contracts import task_contract_registry, contract_uri, CONTRACT_URI_TEMPLATE

logger = logging.getLogger(__name__)

DISPATCH_TOOL_DESCRIPTION = (
    "Única apertura de ingesta del Córtex Asíncrono (ADR-005): toda escritura "
    "mutante del sistema entra como tarea asíncrona por esta tool. Semántica "
    "write-then-read: el dispatch devuelve PENDING con task_id y la consistencia "
    "es eventual — consulta el estado por task_id → done, NUNCA lo infier de una "
    "búsqueda en memoria. Cada task_type tiene un contrato de payload que se "
    "valida aquí de forma síncrona: si el payload viola el contrato, la llamada "
    "falla con isError y el contrato inline — clase de fallo NO reintentable (no "
    "esperes que reintentar lo arregle; corrige el payload). Catálogo de "
    "contratos: " + CONTRACT_URI_TEMPLATE + " (resource servido por ADR-006-002; "
    "hasta entonces el error inline incluye la esquema del contrato)."
)

def get_task_tools() -> list[Tool]:
    """Devuelve la lista de herramientas de tareas para el registro central."""
    return [
        Tool(
            name="coreai_dispatch_task",
            description=DISPATCH_TOOL_DESCRIPTION,
            inputSchema=TaskDispatchRequest.model_json_schema()
        )
    ]

def _format_contract_violation(task_type, error: ValidationError) -> str:
    """
    Error autónomo de la frontera (ADR-006-003, #44): violaciones formateadas +
    URI del contrato + esquema compacta inline. Debe bastar para que un agente
    se autocorriga en-loop sin leer código fuente.
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
        f"El payload viola el contrato de '{task_type}' (ADR-006-003). "
        f"La tarea NO se encoló: cero filas nuevas en tasks y ninguna "
        f"sesión/entidad auto-vivificada para este intento.\n"
        f"Violaciones:\n"
        + "\n".join(violations)
        + f"\nContrato del tipo: {contract_uri(task_type)} "
        f"(URI del resource, servida por ADR-006-002; por ahora usa la esquema inline).\n"
        f"Esquema del contrato: {schema_inline}\n"
        f"Corrige el task_payload para cumplir la esquema y reintenta el dispatch."
    )

async def handle_task_tool(name: str, arguments: dict) -> dict:
    """Manejador específico para las herramientas de tareas."""
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
                f"El tipo de tarea '{request_data.task_type}' no tiene contrato registrado "
                f"(ADR-006-001). Sin contrato no hay despacho. "
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
