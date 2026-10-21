from typing import Generic, TypeVar, Type, Optional, Sequence, Any
from uuid import UUID
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from src.db.database import Base
from src.db.models import Entity, Session, Message, Task, Event, LLMAudit, TaskStatus

# Definimos una variable de tipo vinculada a nuestra base declarativa de SQLAlchemy
ModelType = TypeVar("ModelType", bound=Base)

class BaseRepository(Generic[ModelType]):
    """
    Repositorio genérico puro. 
    Agnóstico al dominio vectorial. Solo ejecuta operaciones atómicas en PostgreSQL.
    """
    def __init__(self, model: Type[ModelType], session: AsyncSession):
        self.model = model
        self.session = session

    async def get(self, id: UUID) -> Optional[ModelType]:
        """Recupera un registro por su UUID primario."""
        result = await self.session.execute(select(self.model).where(self.model.id == id))
        return result.scalar_one_or_none()

    async def get_all(self) -> Sequence[ModelType]:
        """Recupera todos los registros (usar con precaución o añadir paginación después)."""
        result = await self.session.execute(select(self.model))
        return result.scalars().all()

    async def create(self, **kwargs: Any) -> ModelType:
        """
        Instancia y añade un nuevo registro.
        Hace flush() para propagar a la DB y generar el UUID, pero NO comitea la transacción.
        """
        instance = self.model(**kwargs)
        self.session.add(instance)
        await self.session.flush()
        return instance

    async def update(self, instance: ModelType, **kwargs: Any) -> ModelType:
        """Actualiza atributos de una instancia existente y sincroniza con la DB."""
        for key, value in kwargs.items():
            setattr(instance, key, value)
        await self.session.flush()
        return instance

    async def delete(self, instance: ModelType) -> None:
        """Marca la instancia para borrado en la transacción actual."""
        await self.session.delete(instance)
        await self.session.flush()


# =====================================================================
# Repositorios Específicos de Entidad
# =====================================================================

class EntityRepository(BaseRepository[Entity]):
    def __init__(self, session: AsyncSession):
        super().__init__(Entity, session)

class SessionRepository(BaseRepository[Session]):
    def __init__(self, session: AsyncSession):
        super().__init__(Session, session)

class MessageRepository(BaseRepository[Message]):
    def __init__(self, session: AsyncSession):
        super().__init__(Message, session)

class TaskRepository(BaseRepository[Task]):
    def __init__(self, session: AsyncSession):
        super().__init__(Task, session)

    async def claim_next_task(self) -> Task | None:
        """
        Fase 1: Búsqueda y bloqueo atómico.
        Utiliza UPDATE ... RETURNING con una subquery SKIP LOCKED.
        Cero ORM, 100% transaccional nativo de PostgreSQL.
        """
        subq = (
            select(Task.id)
            .where(Task.status == TaskStatus.PENDING)
            .order_by(Task.created_at.asc())
            .with_for_update(skip_locked=True)
            .limit(1)
            .scalar_subquery()
        )

        stmt = (
            update(Task)
            .where(Task.id == subq)
            .values({Task.status: TaskStatus.RUNNING})
            .returning(Task)
        )
        
        result = await self.session.execute(stmt)
        task = result.scalar_one_or_none()
        
        await self.session.commit()
        return task

    async def complete_task(self, task_id: UUID) -> None:
        """Fase 3 (Éxito): Marca la tarea como COMPLETED atómicamente."""
        stmt = (
            update(Task)
            .where(Task.id == task_id)
            .values({Task.status: TaskStatus.COMPLETED})
        )
        await self.session.execute(stmt)
        await self.session.commit()

    async def fail_task(self, task_id: UUID, error_msg: str) -> None:
        """Fase 3 (Fallo): Marca la tarea como FAILED y guarda la traza en el JSONB."""
        # 1. Recuperamos solo el JSONB actual
        result = await self.session.execute(select(Task.payload).where(Task.id == task_id))
        current_payload = result.scalar_one_or_none()
        
        # 2. Defensa contra payloads corruptos
        payload_copy = dict(current_payload) if isinstance(current_payload, dict) else {}
        payload_copy["error_trace"] = error_msg
        
        # 3. Update atómico sin tocar el ORM, mapeando columnas de forma explícita
        stmt = (
            update(Task)
            .where(Task.id == task_id)
            .values({
                Task.status: TaskStatus.FAILED,
                Task.payload: payload_copy
            })
        )
        await self.session.execute(stmt)
        await self.session.commit()

class EventRepository(BaseRepository[Event]):
    def __init__(self, session: AsyncSession):
        super().__init__(Event, session)

class LLMAuditRepository(BaseRepository[LLMAudit]):
    def __init__(self, session: AsyncSession):
        super().__init__(LLMAudit, session)