import logging
import uuid
from sqlalchemy.ext.asyncio import AsyncSession

from src.repositories.relational import TaskRepository, SessionRepository
from src.schemas.tasks import TaskDispatchRequest, TaskDispatchResponse

logger = logging.getLogger(__name__)

class TaskManager:
    """
    Orquestador Transaccional para la Ingesta de Tareas.

    [ADR-012-002] #113: la identidad NO se resuelve aquí — la frontera la
    resuelve contra el registro del despliegue (ADR-012-001) y viaja YA
    resuelta. Muere la auto-vivificación de entidades: ningún despacho crea
    identidad (el único implícito, system_default, lo garantiza el bootstrap).
    La sesión huérfana se sigue vivificando, ligada a la entidad resuelta.

    [ADR-012-003] #114: el actor tampoco se resuelve aquí — la frontera fija
    el free-string de trazabilidad (declarado o default: el slug de la
    entidad resuelta) y el manager solo lo ESTAMPA en la fila (re-home del
    #115): columna de primer nivel, JAMÁS enterrado en el JSONB del payload.
    """
    def __init__(
        self,
        session: AsyncSession,
        task_repo: TaskRepository,
        session_repo: SessionRepository
    ):
        self.session = session
        self.task_repo = task_repo
        self.session_repo = session_repo

    async def dispatch_task(
        self, request: TaskDispatchRequest, entity_id: uuid.UUID, actor: str
    ) -> TaskDispatchResponse:
        """
        Ingiere la tarea YA resuelta en identidad (ADR-012-002) y YA resuelta
        en actor (ADR-012-003): la 'entity_id' y el 'actor' los resolvió la
        frontera y aquí solo se estampan en PRIMER NIVEL relacional — la fila
        queda titulada al despachante y con la trazabilidad de quién despachó,
        del dispatch a la auditoría de la DLQ.
        """
        try:
            resolved_session_id = request.session_id

            # 1. VIVIFICACIÓN DE SESIÓN HUÉRFANA. Límite exacto de la
            #    auto-vivificación tras el ADR-012-002: sesiones sí (una
            #    conversación puede empezar sin ceremonia), entidades JAMÁS.
            if not resolved_session_id:
                logger.info("Vivificación: sesión huérfana ligada a la entidad resuelta...")
                new_session = await self.session_repo.create(entity_id=entity_id)
                resolved_session_id = new_session.id

            # 2. INYECCIÓN DEL CONTRATO EN EL SCHEMALESS (JSONB)
            # Aseguramos que el task_type viva dentro del payload sin alterar la tabla física
            final_payload = dict(request.task_payload)
            final_payload["task_type"] = request.task_type

            # 3. INGESTA DE LA TAREA CON TITULARIDAD (ADR-012-002) Y ACTOR
            #    (ADR-012-003, re-home del #115): la fila nace del despachante
            #    y con el actor estampado como COLUMNA de primer nivel — la
            #    identidad del sustrato JAMÁS enterrada en el JSONB del
            #    payload. El contrato (extra=forbid) ya rebotó cualquier
            #    'actor' espurio en la frontera: esta es la única pluma.
            new_task = await self.task_repo.create(
                session_id=resolved_session_id,
                entity_id=entity_id,
                actor=actor,
                payload=final_payload
            )

            # 4. CONSOLIDACIÓN ATÓMICA
            await self.session.commit()

            logger.info("Bala Trazadora: Tarea %s (tipo: %s, entidad: %s, actor: %s) inyectada en sesión %s",
                        new_task.id, request.task_type, entity_id, actor, resolved_session_id)

            return TaskDispatchResponse(
                task_id=new_task.id,
                session_id=resolved_session_id,
                entity_id=entity_id,
                actor=actor,
                status=new_task.status.value
            )

        except Exception as e:
            # Cortafuegos transaccional: No dejamos basura a medias
            await self.session.rollback()
            logger.error("Fallo estructural durante la ingesta de la tarea: %s", e)
            raise
