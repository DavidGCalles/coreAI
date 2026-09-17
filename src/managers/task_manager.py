import logging
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Entity
from src.repositories.relational import TaskRepository, SessionRepository, EntityRepository
from src.schemas.tasks import TaskDispatchRequest, TaskDispatchResponse

logger = logging.getLogger(__name__)

class TaskManager:
    """
    Orquestador Transaccional para la Ingesta de Tareas.
    Implementa el patrón de Auto-vivificación para garantizar la integridad
    referencial sin exigir estados previos al cliente externo.
    """
    def __init__(
        self,
        session: AsyncSession,
        task_repo: TaskRepository,
        session_repo: SessionRepository,
        entity_repo: EntityRepository
    ):
        self.session = session
        self.task_repo = task_repo
        self.session_repo = session_repo
        self.entity_repo = entity_repo

    async def dispatch_task(self, request: TaskDispatchRequest) -> TaskDispatchResponse:
        try:
            resolved_session_id = request.session_id

            # 1. AUTO-VIVIFICACIÓN DE SESIÓN Y ENTIDAD
            if not resolved_session_id:
                resolved_entity_id = request.entity_id
                
                # Si tampoco hay entidad, buscamos o creamos el perfil por defecto
                if not resolved_entity_id:
                    stmt = select(Entity).where(Entity.role == 'system_default')
                    result = await self.session.execute(stmt)
                    default_entity = result.scalar_one_or_none()
                    
                    if not default_entity:
                        logger.info("Auto-vivificación: Creando Entity 'system_default'...")
                        default_entity = await self.entity_repo.create(
                            role='system_default',
                            metadata_payload={"description": "Fallback entity for orphaned tasks"}
                        )
                    resolved_entity_id = default_entity.id

                # Creamos la sesión huérfana al vuelo y la vinculamos a la entidad
                logger.info("Auto-vivificación: Creando nueva Session...")
                new_session = await self.session_repo.create(entity_id=resolved_entity_id)
                resolved_session_id = new_session.id
            
            # 2. INYECCIÓN DEL CONTRATO EN EL SCHEMALESS (JSONB)
            # Aseguramos que el task_type viva dentro del payload sin alterar la tabla física
            final_payload = dict(request.task_payload)
            final_payload["task_type"] = request.task_type

            # 3. INGESTA DE LA TAREA
            new_task = await self.task_repo.create(
                session_id=resolved_session_id,
                payload=final_payload
            )

            # 4. CONSOLIDACIÓN ATÓMICA
            await self.session.commit()
            
            logger.info("Bala Trazadora: Tarea %s (tipo: %s) inyectada en sesión %s", 
                        new_task.id, request.task_type, resolved_session_id)
            
            return TaskDispatchResponse(
                task_id=new_task.id,
                session_id=resolved_session_id,
                status=new_task.status.value
            )

        except Exception as e:
            # Cortafuegos transaccional: No dejamos basura a medias
            await self.session.rollback()
            logger.error("Fallo estructural durante la ingesta de la tarea: %s", e)
            raise