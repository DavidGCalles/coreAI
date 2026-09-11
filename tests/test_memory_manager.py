import pytest
import uuid
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import select

from src.db.database import AsyncSessionLocal, engine
from src.db.models import Entity, Event, LLMAudit
from src.repositories.relational import EventRepository, EntityRepository, LLMAuditRepository
from src.managers.memory_manager import HybridMemoryManager
from src.schemas.memory import DomainType, MemorySearchFilters
from src.schemas.llm import EmbeddingResponse, EmbeddingData, EmbeddingUsage

@pytest.fixture
async def db_session():
    """Sesión transaccional limpia para Postgres."""
    await engine.dispose()
    async with AsyncSessionLocal() as session:
        yield session
        await session.rollback()

@pytest.fixture
async def base_entity(db_session):
    """Crea una Entidad física en Postgres para satisfacer la Foreign Key."""
    repo = EntityRepository(db_session)
    entity = await repo.create(role="hybrid_test_subject", metadata_payload={})
    await db_session.commit()
    return entity

@pytest.fixture
def vector_repo_mock():
    """Mock del repo vectorial."""
    return AsyncMock()

@pytest.fixture
def mock_llm_client():
    """Mock del cliente HTTP que devuelve el contrato de datos estricto."""
    client = AsyncMock()
    # Simulamos la respuesta de LiteLLM parseada por Pydantic
    fake_response = EmbeddingResponse(
        model="text-embedding-test",
        data=[EmbeddingData(embedding=[0.1, 0.2, 0.3, 0.4], index=0)],
        usage=EmbeddingUsage(prompt_tokens=15, total_tokens=15)
    )
    client.generate_embedding.return_value = fake_response
    return client


@pytest.mark.asyncio
async def test_hybrid_manager_happy_path_with_audit(db_session, base_entity, vector_repo_mock, mock_llm_client):
    """Valida la inyección doble y que la auditoría de tokens se registre en la misma transacción."""
    
    event_repo = EventRepository(db_session)
    audit_repo = LLMAuditRepository(db_session)
    manager = HybridMemoryManager(db_session, event_repo, vector_repo_mock, audit_repo, mock_llm_client)
    
    test_content = "El cielo es azul oscuro"
    
    # --- INGESTA ---
    event_id = await manager.add_memory(
        entity_id=base_entity.id,
        domain=DomainType.SYSTEM,
        content=test_content
    )
    
    assert event_id is not None
    mock_llm_client.generate_embedding.assert_called_once()
    vector_repo_mock.upsert.assert_called_once()
    
    # --- VERIFICACIÓN DE AUDITORÍA ---
    # Comprobamos que el gasto se ha registrado en la tabla llm_audit apuntando a nuestro usuario
    stmt = select(LLMAudit).where(LLMAudit.entity_id == base_entity.id)
    result = await db_session.execute(stmt)
    audits = result.scalars().all()
    
    assert len(audits) == 1, "No se generó el registro de auditoría en PostgreSQL."
    assert audits[0].model_used == "text-embedding-test"
    assert audits[0].total_tokens == 15
    assert audits[0].completion_tokens == 0


@pytest.mark.asyncio
async def test_hybrid_manager_rollback_on_qdrant_failure(db_session, base_entity, vector_repo_mock, mock_llm_client):
    """Sabotea Qdrant y verifica el Rollback total (Evento y Auditoría mueren)."""
    
    event_repo = EventRepository(db_session)
    audit_repo = LLMAuditRepository(db_session)
    manager = HybridMemoryManager(db_session, event_repo, vector_repo_mock, audit_repo, mock_llm_client)
    
    target_entity_id = base_entity.id
    
    # 1. Saboteamos el VectorRepository
    vector_repo_mock.upsert.side_effect = Exception("¡Explosión termonuclear en Qdrant!")
    
    # 2. Disparamos la inyección
    with pytest.raises(Exception, match="Explosión termonuclear"):
        await manager.add_memory(
            entity_id=target_entity_id,
            domain=DomainType.SYSTEM,
            content="Este recuerdo nunca existirá"
        )
    
    # 3. VERIFICACIÓN DE CONSISTENCIA DE EVENTOS
    stmt_events = select(Event).where(Event.entity_id == target_entity_id)
    result_events = await db_session.execute(stmt_events)
    assert len(result_events.scalars().all()) == 0, "¡Fuga! El evento se guardó."
    
    # 4. VERIFICACIÓN DE CONSISTENCIA DE AUDITORÍA (No pagamos por algo que falló)
    stmt_audit = select(LLMAudit).where(LLMAudit.entity_id == target_entity_id)
    result_audit = await db_session.execute(stmt_audit)
    assert len(result_audit.scalars().all()) == 0, "¡Fuga financiera! La auditoría sobrevivió al rollback."