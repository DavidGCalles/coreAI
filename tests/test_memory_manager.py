import pytest
import uuid
from unittest.mock import AsyncMock, patch
from qdrant_client.http.models import ScoredPoint
from sqlalchemy import select

from src.db.database import AsyncSessionLocal, engine
from src.db.models import Entity, Event
from src.repositories.relational import EventRepository, EntityRepository
from src.managers.memory_manager import HybridMemoryManager
from src.schemas.memory import DomainType, MemorySearchFilters

@pytest.fixture
async def db_session():
    """Sesión transaccional limpia para Postgres."""
    await engine.dispose()
    async with AsyncSessionLocal() as session:
        yield session
        await session.rollback()

@pytest.fixture
async def base_entity(db_session):
    """Crea una Entidad física en Postgres para satisfacer la Foreign Key de Event."""
    repo = EntityRepository(db_session)
    entity = await repo.create(role="hybrid_test_subject", metadata_payload={})
    await db_session.commit()
    return entity

@pytest.fixture
def vector_repo_mock():
    """Mock del repo vectorial para no depender de Qdrant en este test de orquestación."""
    return AsyncMock()


async def test_hybrid_manager_happy_path(db_session, base_entity, vector_repo_mock):
    """Valida la inyección doble y la hidratación (Scatter-Gather)."""
    event_repo = EventRepository(db_session)
    manager = HybridMemoryManager(db_session, event_repo, vector_repo_mock)
    
    test_content = "El cielo es azul oscuro"
    fake_vector = [0.1, 0.2, 0.3, 0.4]
    
    # 1. Mockeamos la llamada a LiteLLM (Epic 3)
    with patch.object(manager, '_get_embedding', new_callable=AsyncMock) as mock_embedding:
        mock_embedding.return_value = fake_vector
        
        # --- INGESTA ---
        event_id = await manager.add_memory(
            entity_id=base_entity.id,
            domain=DomainType.SYSTEM,
            content=test_content
        )
        
        assert event_id is not None
        vector_repo_mock.upsert.assert_called_once()
        
        # --- BÚSQUEDA (Scatter-Gather) ---
        # Simulamos que Qdrant devuelve nuestro UUID con un score alto
        vector_repo_mock.search.return_value = [
            ScoredPoint(id=str(event_id), version=1, score=0.99, payload={})
        ]
        
        filters = MemorySearchFilters(
            tenant_id=str(base_entity.id),
            query="color del cielo"  # Pydantic exige este campo
        )
        results = await manager.search_memory(base_entity.id, "color del cielo", filters)
        
        assert len(results) == 1, "La hidratación desde Postgres falló."
        assert isinstance(results[0], Event), "El manager devolvió algo que no es un objeto relacional puro."
        assert results[0].id == event_id
        assert results[0].content == test_content


async def test_hybrid_manager_rollback_on_qdrant_failure(db_session, base_entity, vector_repo_mock):
    """La prueba de fuego: Sabotear Qdrant y verificar el Rollback en Postgres."""
    event_repo = EventRepository(db_session)
    manager = HybridMemoryManager(db_session, event_repo, vector_repo_mock)
    
    # EL ANTÍDOTO: Guardamos el ID en memoria pura de Python.
    # Así evitamos el lazy-loading síncrono cuando el rollback expire la base_entity.
    target_entity_id = base_entity.id
    
    with patch.object(manager, '_get_embedding', new_callable=AsyncMock) as mock_embedding:
        mock_embedding.return_value = [0.1, 0.2, 0.3, 0.4]
        
        # 1. Saboteamos el VectorRepository
        vector_repo_mock.upsert.side_effect = Exception("¡Explosión termonuclear en Qdrant!")
        
        # 2. Disparamos la inyección
        with pytest.raises(Exception, match="Explosión termonuclear"):
            await manager.add_memory(
                entity_id=target_entity_id,  # <--- Usamos la variable local pura
                domain=DomainType.SYSTEM,
                content="Este recuerdo nunca existirá"
            )
        
        # 3. VERIFICACIÓN DE CONSISTENCIA
        stmt = select(Event).where(Event.entity_id == target_entity_id) # <--- Usamos la variable local pura
        result = await db_session.execute(stmt)
        events_in_db = result.scalars().all()
        
        assert len(events_in_db) == 0, "¡Fuga de datos! Postgres comiteó la transacción ignorando el fallo de Qdrant."