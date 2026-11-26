import json
import logging
from mcp.types import Tool, CallToolRequestParams
from pydantic import ValidationError

from sqlalchemy import select

from src.db.database import AsyncSessionLocal
from src.db.entities_config import SYSTEM_DEFAULT_SLUG
from src.db.models import Entity, TaskStatus
from src.repositories.relational import TaskRepository, SessionRepository
from src.managers.task_manager import TaskManager
from src.schemas.tasks import (
    TaskDispatchRequest,
    TaskGetRequest,
    TaskListItem,
    TaskListRequest,
    TaskListResponse,
    TaskRetryRequest,
    TaskRetryResponse,
    TaskStatusResponse,
)
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
    "reintentar lo arregle; corrige el payload). Identidad (ADR-012-002): "
    "el argumento opcional 'entity' declara quién despacha, con el slug con "
    "el que vive registrado en el despliegue; la frontera lo resuelve y la "
    "respuesta devuelve SIEMPRE la entity_id resuelta (system_default "
    "explícito si no declaras ninguna). Un slug declarado y no registrado "
    "rebota aquí mismo con el catálogo de entidades válidas: mismo fallo "
    "determinista que el contrato, la tarea no se crea y reintentar no "
    "sirve. Trazabilidad (ADR-012-003): el argumento opcional 'actor' es "
    "un free-string de quién despacha dentro del namespace de la entidad "
    "— sin censo ni validación semántica (autenticar humanos es del edge, "
    "JAMÁS del core); la respuesta, el contexto de la tarea y cada entrada "
    "de error_history llevan el actor estampado (por omisión, el slug de "
    "la entidad resuelta). El actor JAMÁS genera veneno: el único veneno "
    "de identidad es el de entidad no registrada. Catálogo de contratos: "
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

LIST_TASKS_TOOL_DESCRIPTION = (
    "Listado read-only de tareas del Córtex Asíncrono por estado (cero "
    "mutación). La DLQ lógica del sistema es el estado FAILED: tareas "
    "legítimas que fallaron en ejecución y agotaron sus reintentos "
    "automáticos. Cada ítem lleva retry_count, error_history (auditoría "
    "intento a intento) y su edad, en orden FIFO (la más antigua primero) y "
    "con límite (default 50, techo 200). Una FAILED se resuelve leyendo su "
    "error_trace y error_history: si el fallo fue del sistema y ya está "
    "corregido, reinyecta con coreai_retry_failed_task; si el problema es "
    "el contenido, la reinyección solo pospondrá el mismo desenlace."
)

RETRY_FAILED_TASK_TOOL_DESCRIPTION = (
    "Reinyección manual de una tarea FAILED de la DLQ: vuelve a PENDING "
    "con retry_count=0 y disponible YA (available_at=NULL) — el worker la "
    "reclamará como si acabara de entrar. SOLO FAILED es reinyectable: "
    "cualquier otro estado es un error explícito, nunca silencio. El "
    "error_history queda intacto: la reinyección resetea la mecánica de "
    "reintentos, no la auditoría. Reinyectar NO garantiza éxito: si la "
    "causa del fallo sigue viva, la tarea recorrerá de nuevo sus "
    "reintentos hasta volver a la DLQ."
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
        ),
        Tool(
            name="coreai_list_tasks",
            description=LIST_TASKS_TOOL_DESCRIPTION,
            inputSchema=TaskListRequest.model_json_schema()
        ),
        Tool(
            name="coreai_retry_failed_task",
            description=RETRY_FAILED_TASK_TOOL_DESCRIPTION,
            inputSchema=TaskRetryRequest.model_json_schema()
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

def _format_unregistered_entity(declared: str, valid_slugs: list[str]) -> str:
    """
    Error autónomo de la frontera de identidad (ADR-012-002, #113): el slug
    declarado no vive en el registro del despliegue (ADR-012-001, el
    oráculo). Veneno determinista — misma familia de fallo que la
    validación de forma del ADR-006: reintentar el mismo argumento no sirve.
    """
    catalogo = ", ".join(valid_slugs) or "ninguna"
    return (
        f"La entidad declarada '{declared}' no está registrada en este "
        f"despliegue. La identidad se gobierna en el despliegue (YAML "
        f"montado + bootstrap, ADR-012-001): un slug declarado y no "
        f"registrado es veneno determinista, no un fallo transitorio. "
        f"La tarea NO se encoló: cero filas nuevas en tasks y ninguna sesión "
        f"o entidad auto-vivificada para este intento. "
        f"Entidades registradas: {catalogo}. "
        f"Corrige el slug, declara la entidad en el fichero del despliegue, "
        f"o despacha sin 'entity' (system_default implícito)."
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
                # 2.1 Frontera de identidad (ADR-012-002, #113): la misma
                #     frontera que valida la forma resuelve QUIÉN despacha,
                #     contra el registro del despliegue (ADR-012-001, el
                #     oráculo). El veneno (D4) aplica por igual a TODAS las
                #     task types: la frontera es una sola.
                declarada = (
                    request_data.entity if request_data.entity is not None
                    else SYSTEM_DEFAULT_SLUG
                )
                result = await session.execute(
                    select(Entity).where(Entity.slug == declarada)
                )
                resolved_entity = result.scalar_one_or_none()

                if resolved_entity is None:
                    if request_data.entity is None:
                        # system_default ausente NO es veneno: es fallo
                        # estructural — el bootstrap del ADR-012-001
                        # garantiza el implícito y aquí solo se llega con
                        # un despliegue que no completó su provisión.
                        logger.error(
                            "Fallo estructural: '%s' ausente del registro y el "
                            "bootstrap del ADR-012-001 lo garantiza",
                            SYSTEM_DEFAULT_SLUG,
                        )
                        return {
                            "content": [{
                                "type": "text",
                                "text": (
                                    "Fallo estructural: la identidad implícita "
                                    f"'{SYSTEM_DEFAULT_SLUG}' no está registrada. "
                                    "El bootstrap la garantiza al arranque "
                                    "(ADR-012-001): la provisión de entidades de "
                                    "este nodo no se completó — revisa el despliegue."
                                )
                            }],
                            "isError": True
                        }
                    # Caso 2 (D4, ADR-012-002): declarada y NO registrada →
                    # veneno determinista con catálogo inline (estilo
                    # ADR-006). Todo read-only: nada se encoló y la sesión
                    # efímera se cierra sin commit.
                    catalog_result = await session.execute(
                        select(Entity.slug)
                        .where(Entity.slug.is_not(None))
                        .order_by(Entity.slug.asc())
                    )
                    valid_slugs = [row[0] for row in catalog_result]
                    logger.warning(
                        "Dispatch rechazado por entidad no registrada: %s (válidas: %s)",
                        request_data.entity, valid_slugs,
                    )
                    return {
                        "content": [{
                            "type": "text",
                            "text": _format_unregistered_entity(
                                request_data.entity, valid_slugs
                            )
                        }],
                        "isError": True
                    }

                # 2.2 Frontera de actor (ADR-012-003, #114): free-string de
                #     trazabilidad, resuelto aquí mismo. La forma (techo de
                #     longitud, MAX_ACTOR_LENGTH) ya la validó Pydantic al
                #     parsear el sobre; vacío o solo espacios se normalizan a
                #     ausente — el actor JAMÁS genera veneno (D4 es de la
                #     ENTIDAD, no del actor: autenticar humanos es del edge).
                #     Default por omisión: el slug de la entidad resuelta
                #     ('declarada' ES el slug contra el que la frontera acaba
                #     de resolver, también en el caso ausente).
                actor_explicito = (request_data.actor or "").strip()
                actor = actor_explicito if actor_explicito else declarada

                task_repo = TaskRepository(session)
                session_repo = SessionRepository(session)

                manager = TaskManager(session, task_repo, session_repo)
                response = await manager.dispatch_task(
                    request_data, entity_id=resolved_entity.id, actor=actor
                )

                # Contrato de salida serializado: el cliente lee el session_id
                # autogenerado, la entity_id RESUELTA (ADR-012-002) y el actor
                # estampado (ADR-012-003) — ni la resolución ni el estampado
                # son silenciosos.
                return {
                    "content": [{"type": "text", "text": response.model_dump_json(indent=2)}],
                    "isError": False
                }
        except Exception as e:
            logger.error("Fallo interno del motor de tareas: %s", e)
            return {"content": [{"type": "text", "text": f"Fallo estructural: {str(e)}"}], "isError": True}
    
    # =================================================================
    # coreai_list_tasks (4.4-003, #105): listado read-only por estado.
    # La DLQ lógica es status=FAILED con su perfil de auditoría a la vista.
    # =================================================================
    if name == "coreai_list_tasks":
        try:
            request_data = TaskListRequest.model_validate(arguments)
        except Exception as e:
            logger.error("Argumentos malformados en list_tasks: %s", e)
            return {"content": [{"type": "text", "text": f"Error de validación: {str(e)}"}], "isError": True}

        try:
            async with AsyncSessionLocal() as session:
                task_repo = TaskRepository(session)
                rows = await task_repo.list_by_status(
                    TaskStatus(request_data.status), request_data.limit
                )

                items = []
                for task, age_seconds in rows:
                    # task_type, error_trace y error_history viven dentro del
                    # JSONB payload (los inyectan TaskManager y fail_task):
                    # extracción defensiva, mismo patrón que coreai_get_task.
                    payload = task.payload if isinstance(task.payload, dict) else {}
                    history = payload.get("error_history")
                    if not isinstance(history, list):
                        history = []
                    items.append(
                        TaskListItem(
                            task_id=task.id,
                            session_id=task.session_id,
                            task_type=payload.get("task_type"),
                            status=task.status.value,
                            retry_count=task.retry_count or 0,
                            error_trace=payload.get("error_trace"),
                            error_history=history,
                            created_at=task.created_at,
                            updated_at=task.updated_at,
                            age_seconds=int(age_seconds),
                        )
                    )

                response = TaskListResponse(
                    status=request_data.status,
                    count=len(items),
                    tasks=items,
                )
                # Read-only estricto: solo un SELECT cruzó la frontera; la
                # sesión efímera se cierra sin commit ni flush de escritura.
                return {
                    "content": [{"type": "text", "text": response.model_dump_json(indent=2)}],
                    "isError": False
                }
        except Exception as e:
            logger.error("Fallo interno en el listado de tareas: %s", e)
            return {"content": [{"type": "text", "text": f"Fallo estructural: {str(e)}"}], "isError": True}

    # =================================================================
    # coreai_retry_failed_task (4.4-003, #105): reinyección manual de una
    # FAILED de la DLQ — vuelve a PENDING desde cero.
    # =================================================================
    if name == "coreai_retry_failed_task":
        try:
            request_data = TaskRetryRequest.model_validate(arguments)
        except Exception as e:
            logger.error("Argumentos malformados en retry_failed_task: %s", e)
            return {"content": [{"type": "text", "text": f"Error de validación: {str(e)}"}], "isError": True}

        try:
            async with AsyncSessionLocal() as session:
                task_repo = TaskRepository(session)
                desenlace = await task_repo.reinject_failed_task(request_data.task_id)

                if desenlace is None:
                    # Distinción honesta (4.4-003, #105): fila inexistente vs
                    # estado no reinyectable — el error dice cuál es cuál.
                    task = await task_repo.get(request_data.task_id)
                    if task is None:
                        logger.warning("Reinyección de tarea inexistente: %s", request_data.task_id)
                        text = (
                            f"La tarea {request_data.task_id} no existe. "
                            "No hay fila con ese task_id: verifica que el UUID "
                            "venga de coreai_list_tasks o coreai_get_task."
                        )
                    else:
                        status = task.status.value if hasattr(task.status, "value") else str(task.status)
                        logger.warning(
                            "Reinyección rechazada: la tarea %s está en estado %s",
                            request_data.task_id, status,
                        )
                        text = (
                            f"La tarea {request_data.task_id} está en estado {status}: "
                            "solo una FAILED de la DLQ es reinyectable. "
                            "Una tarea viva no necesita reinyección — consulta su "
                            "estado con coreai_get_task."
                        )
                    return {"content": [{"type": "text", "text": text}], "isError": True}

                response = TaskRetryResponse(
                    task_id=desenlace["task_id"],
                    session_id=desenlace["session_id"],
                    status="PENDING",
                    retry_count=desenlace["retry_count"],
                    available_at=None,
                )
                return {
                    "content": [{"type": "text", "text": response.model_dump_json(indent=2)}],
                    "isError": False
                }
        except Exception as e:
            logger.error("Fallo interno en la reinyección de tarea: %s", e)
            return {"content": [{"type": "text", "text": f"Fallo estructural: {str(e)}"}], "isError": True}

    raise ValueError(f"Herramienta {name} no es gestionada por task_tools.")

