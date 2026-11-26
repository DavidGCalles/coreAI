import random
from typing import Generic, TypeVar, Type, Optional, Sequence, Any
from uuid import UUID
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, update, or_, extract
from sqlalchemy.sql import func
from sqlalchemy.ext.asyncio import AsyncSession
from src.db.database import Base
from src.db.models import Entity, Session, Message, Task, Event, LLMAudit, TaskStatus
from src.managers.config_manager import config_manager
from src.schemas.memory import DomainType

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

def _backoff_reintento(retry_count: int, config: dict[str, Any]) -> float:
    """
    Segundos de espera del próximo reintento (4.4-002 #104): exponencial
    base·2^(n-1) con techo cap y jitter proporcional ±jitter.

    Esto solo calcula el INTERVALO: el vencimiento real (available_at) lo
    asigna Postgres (func.now(), consciente de zona) en retry_task — el reloj
    que decide es el de la base de datos, no el del worker.
    """
    base = config["backoff_base_seconds"]
    cap = config["backoff_cap_seconds"]
    jitter = config["backoff_jitter"]
    espera = min(cap, base * (2 ** (retry_count - 1)))
    espera *= 1.0 + random.uniform(-jitter, jitter)
    return max(espera, 0.0)


class TaskRepository(BaseRepository[Task]):
    def __init__(self, session: AsyncSession):
        super().__init__(Task, session)

    async def claim_next_task(self) -> Task | None:
        """
        Fase 1: Búsqueda y bloqueo atómico.
        Utiliza UPDATE ... RETURNING con una subquery SKIP LOCKED.
        Cero ORM, 100% transaccional nativo de PostgreSQL.

        Disponibilidad (4.4-001 #103): solo se reclaman las PENDING cuyo
        available_at sea NULL (nunca diferida) o ya vencido — una tarea
        diferida espera en la cola sin que el claim la robe. La comparación
        de vencimiento la hace Postgres (func.now(), consciente de zona).
        """
        subq = (
            select(Task.id)
            .where(
                Task.status == TaskStatus.PENDING,
                or_(Task.available_at.is_(None), Task.available_at <= func.now()),
            )
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

    async def retry_task(self, task_id: UUID, error_msg: str) -> dict[str, Any] | None:
        """
        Fase 3 (Fallo transitorio, 4.4-002 #104): reintento con backoff o FAILED final.

        Todo por UUID, sin instancias ORM (misma forma que fail_task — lección
        del rollback #61). Si quedan reintentos (retry_count < max_retries):
        +1, status vuelve a PENDING, available_at = now + backoff (el
        vencimiento lo asigna Postgres con su propio reloj, consciente de
        zona), y la traza appenda a error_history del payload — el payload
        original viaja intacto. Agotados los reintentos: FAILED con la traza
        final (la DLQ lógica del 4.4: FAILED + historial completo).

        La tarea llega aquí RUNNING desde el único worker que la reclamó:
        no hay carrera concurrente sobre la fila.

        Devuelve el desenlace para el log del worker: {"outcome": "RETRY" |
        "FAILED", "retry_count": int, "available_at": datetime | None}.
        None si la fila no existe (el worker solo lo registra).
        """
        result = await self.session.execute(
            select(Task.retry_count, Task.payload, Task.actor).where(Task.id == task_id)
        )
        fila = result.one_or_none()
        if fila is None:
            return None
        retry_count = fila.retry_count or 0
        current_payload = fila.payload
        current_actor = fila.actor

        config = config_manager.get_retry_config()
        max_retries = config["max_retries"]

        # Historial append-only (4.4-002 #104): cada intento fallido deja su
        # entrada con intento, instante y traza — la auditoría de la DLQ
        # lógica y su lectura MCP (4.4-003) se apoyan aquí.
        # [ADR-012-003] #114: la entrada se enriquece con el actor — el mismo
        # free-string que el dispatch estampó en la COLUMNA de primer nivel
        # (re-home del #115), leído aquí para que cada intento de la DLQ diga
        # QUIÉN lo despachó (apto Grafana). Tareas históricas anteriores al
        # re-home: columna NULL → None; la auditoría no se reescribe, solo se
        # enriquece hacia adelante.
        payload_copy = dict(current_payload) if isinstance(current_payload, dict) else {}
        history = list(payload_copy.get("error_history", []))
        history.append({
            "attempt": retry_count + 1,
            "at": datetime.now(timezone.utc).isoformat(),
            "trace": error_msg,
            "actor": current_actor,
        })
        payload_copy["error_history"] = history
        payload_copy["error_trace"] = error_msg

        if retry_count < max_retries:
            nuevo_contador = retry_count + 1
            espera = _backoff_reintento(nuevo_contador, config)
            stmt = (
                update(Task)
                .where(Task.id == task_id)
                .values({
                    Task.status: TaskStatus.PENDING,
                    Task.retry_count: nuevo_contador,
                    Task.available_at: func.now() + timedelta(seconds=espera),
                    Task.payload: payload_copy,
                })
                .returning(Task.available_at)
            )
            result = await self.session.execute(stmt)
            await self.session.commit()
            return {
                "outcome": "RETRY",
                "retry_count": nuevo_contador,
                "available_at": result.scalar_one(),
            }

        # Reintentos agotados: FAILED con el historial completo y el payload
        # original intacto para auditoría o reinyección manual (4.4-003).
        # available_at se limpia: una FAILED no tiene vencimiento que cumplir.
        stmt = (
            update(Task)
            .where(Task.id == task_id)
            .values({
                Task.status: TaskStatus.FAILED,
                Task.available_at: None,
                Task.payload: payload_copy,
            })
        )
        await self.session.execute(stmt)
        await self.session.commit()
        return {"outcome": "FAILED", "retry_count": retry_count, "available_at": None}

    async def list_by_status(self, status: TaskStatus, limit: int) -> Sequence[Any]:
        """
        Listado read-only por estado (4.4-003 #105): la DLQ lógica del sistema
        es status=FAILED, con su perfil de auditoría completo (retry_count,
        error_history y edad) a la vista de la frontera.

        Orden FIFO por created_at — una DLQ se trabaja de la más vieja a la
        más nueva, como el claim. La edad la calcula el reloj de Postgres
        (EXTRACT(EPOCH FROM now() - created_at)), el mismo que firma
        available_at: la frontera no aporta su propio reloj (misma doctrina
        que claim_next_task).

        NO muta: solo un SELECT, sin commit — la sesión efímera del llamador
        se cierra sola. Devuelve filas (Task, age_seconds) para que la
        frontera arme el ítem sin exponer el payload crudo.
        """
        edad = extract("epoch", func.now() - Task.created_at).label("age_seconds")
        stmt = (
            select(Task, edad)
            .where(Task.status == status)
            .order_by(Task.created_at.asc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return result.all()

    async def reinject_failed_task(self, task_id: UUID) -> dict[str, Any] | None:
        """
        Reinyección manual de una FAILED de la DLQ (4.4-003 #105): UPDATE
        condicional → PENDING, retry_count=0, available_at=NULL (elegible YA
        para el claim del worker).

        El error_history del payload queda INTACTO (decisión de David, 17
        nov): la reinyección resetea la mecánica de reintentos, no la
        auditoría — quien necesita el porqué lo sigue teniendo en el
        historial. Todo por UUID, sin instancias ORM (misma forma que
        retry_task — lección del rollback #61).

        La condición status=FAILED vive en el propio WHERE: la atomicidad es
        la idempotencia — si el desenlace ya no es FAILED (el worker ganó la
        carrera, o la tarea acaba de ser reinyectada), el rowcount es 0 y
        NADIE aplica el reset dos veces sobre la misma fila.

        Devuelve {"task_id", "session_id", "retry_count": 0} si reinyectó;
        None si la fila no era reinyectable — el llamador distingue leyendo
        el estado real antes de responder, nunca en silencio.
        """
        stmt = (
            update(Task)
            .where(Task.id == task_id, Task.status == TaskStatus.FAILED)
            .values({
                Task.status: TaskStatus.PENDING,
                Task.retry_count: 0,
                Task.available_at: None,
            })
            .returning(Task.session_id)
        )
        result = await self.session.execute(stmt)
        await self.session.commit()
        session_id = result.scalar_one_or_none()
        if session_id is None:
            return None
        return {"task_id": task_id, "session_id": session_id, "retry_count": 0}

    async def set_payload_fields(self, task_id: UUID, fields: dict[str, Any]) -> bool:
        """
        Actualiza claves del JSONB payload de una task, atómicamente y por
        UUID, sin instancias ORM (misma forma que fail_task).

        Es la simetría relacional del evento de corrección de memoria
        (ADR-009, #89): en los puntos de imagen la task original ES el
        registro relacional del punto y su JSONB lleva el campo corregido;
        y es la pluma de la traza del propio evento de corrección
        (colección localizada + valor previo), append-only: los registros
        origen nunca se reescriben.

        NO comitea: la transacción pertenece al llamador. Devuelve False
        si la fila no existe o su payload no es un objeto JSON (corrupción).
        """
        result = await self.session.execute(
            select(Task.payload).where(Task.id == task_id)
        )
        current_payload = result.scalar_one_or_none()

        if not isinstance(current_payload, dict):
            return False

        payload_copy = dict(current_payload)
        payload_copy.update(fields)
        stmt = (
            update(Task)
            .where(Task.id == task_id)
            .values({Task.payload: payload_copy})
        )
        await self.session.execute(stmt)
        return True

class EventRepository(BaseRepository[Event]):
    def __init__(self, session: AsyncSession):
        super().__init__(Event, session)

    async def list_pending_consolidation(
        self, entity_id: UUID, domain: DomainType, cutoff: datetime
    ) -> Sequence[Event]:
        """
        Eventos episódicos de un inquilino+dominio anteriores al corte de la
        ventana temporal y aún no consolidados (Issue 4.3, #29).

        El filtro consolidated=False es la idempotencia de la consolidación:
        lo ya procesado nunca vuelve a la ventana.
        """
        stmt = (
            select(Event)
            .where(
                Event.entity_id == entity_id,
                Event.domain == domain,
                Event.consolidated.is_(False),
                Event.created_at < cutoff,
            )
            .order_by(Event.created_at.asc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def mark_consolidated(self, event_ids: Sequence[UUID]) -> int:
        """
        Marca los eventos origen como consolidados: UPDATE atómico por UUID,
        sin instancias ORM (misma forma que fail_task). Devuelve las filas marcadas.
        """
        if not event_ids:
            return 0
        stmt = (
            update(Event)
            .where(Event.id.in_(list(event_ids)))
            .values({Event.consolidated: True})
        )
        result = await self.session.execute(stmt)
        return result.rowcount

class LLMAuditRepository(BaseRepository[LLMAudit]):
    def __init__(self, session: AsyncSession):
        super().__init__(LLMAudit, session)
