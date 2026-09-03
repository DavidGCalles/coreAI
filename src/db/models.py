import uuid
import enum
from sqlalchemy import String, Text, Boolean, JSON, ForeignKey, DateTime, Index
from sqlalchemy.dialects.postgresql import UUID, ENUM as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from src.db.database import Base
from src.schemas.memory import DomainType, Visibility

class SessionStatus(str, enum.Enum):
    ACTIVE = 'ACTIVE'
    CLOSED = 'CLOSED'

class TaskStatus(str, enum.Enum):
    PENDING = 'PENDING'
    RUNNING = 'RUNNING'
    COMPLETED = 'COMPLETED'
    FAILED = 'FAILED'

class Entity(Base):
    __tablename__ = 'entities'
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # Identifica si es un humano, un agente externo, o el propio sistema
    role: Mapped[str] = mapped_column(String(50), default='user')
    # Flexibilidad absoluta para configuraciones del nodo (RBAC, preferencias, keys)
    metadata_payload: Mapped[dict] = mapped_column(JSON, default={})
    
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class Session(Base):
    __tablename__ = 'sessions'
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('entities.id', ondelete='CASCADE'))
    
    status: Mapped[SessionStatus] = mapped_column(
        SQLEnum(SessionStatus, name='session_status', create_type=False), 
        default=SessionStatus.ACTIVE
    )
    
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class Message(Base):
    __tablename__ = 'messages'
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('sessions.id', ondelete='CASCADE'))
    
    role: Mapped[str] = mapped_column(String(50))
    content: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(String(255), nullable=True)
    input_type: Mapped[str] = mapped_column(String(50), default='text')
    agent_key: Mapped[str] = mapped_column(String(255), nullable=True)
    consolidated: Mapped[bool] = mapped_column(Boolean, default=False)
    
    visibility: Mapped[Visibility] = mapped_column(
        SQLEnum(Visibility, name='memory_visibility', create_type=False), 
        default=Visibility.PRIVATE
    )
    domain: Mapped[DomainType] = mapped_column(
        SQLEnum(DomainType, name='memory_domain_type', create_type=False), 
        default=DomainType.CONVERSATION
    )
    
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index('idx_messages_session_time', 'session_id', 'created_at'),
    )

class Task(Base):
    __tablename__ = 'tasks'
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('sessions.id', ondelete='CASCADE'))
    
    status: Mapped[TaskStatus] = mapped_column(
        SQLEnum(TaskStatus, name='task_status', create_type=False), 
        default=TaskStatus.PENDING
    )
    payload: Mapped[dict] = mapped_column(JSON, default={})
    
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index('idx_tasks_status', 'status', postgresql_where=(status == 'PENDING')),
    )

class Event(Base):
    __tablename__ = 'events'
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('entities.id', ondelete='CASCADE'))
    
    content: Mapped[str] = mapped_column(Text)
    domain: Mapped[DomainType] = mapped_column(SQLEnum(DomainType, name='memory_domain_type', create_type=False))
    visibility: Mapped[Visibility] = mapped_column(SQLEnum(Visibility, name='memory_visibility', create_type=False))
    consolidated: Mapped[bool] = mapped_column(Boolean, default=False)
    
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index('idx_events_owner', 'entity_id'),
    )