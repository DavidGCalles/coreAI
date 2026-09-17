import pytest
import uuid
from sqlalchemy import select

from src.db.database import AsyncSessionLocal, engine
from src.db.models import Task, Session, Entity
from src.repositories.relational import TaskRepository, SessionRepository, EntityRepository
from src.managers.task_manager import TaskManager
from src.schemas.tasks import TaskDispatchRequest

@pytest.fixture
async def db_session():
    """Sesión transaccional limpia."""
    await engine.dispose()
    async with AsyncSessionLocal() as session:
        yield session
        await session.rollback()

@pytest.mark.asyncio
async def test_task_manager_autovivification(db_session):
    """
    Test de lógica core: Valida que el mánager es capaz de construir 
    el árbol relacional (Entity -> Session -> Task) cuando no recibe IDs.
    """
    # 1. Setup del Mánager
    task_repo = TaskRepository(db_session)
    session_repo = SessionRepository(db_session)
    entity_repo = EntityRepository(db_session)
    manager = TaskManager(db_session, task_repo, session_repo, entity_repo)
    
    # 2. Contrato de entrada (sin session_id ni entity_id)
    request = TaskDispatchRequest(
        task_type="extract_entities",
        task_payload={"raw_text": "Cargando volcado mental."}
    )
    
    # 3. Ejecución
    response = await manager.dispatch_task(request)
    
    # 4. Validaciones de negocio en BD
    result = await db_session.execute(select(Task).where(Task.id == response.task_id))
    task_in_db = result.scalar_one_or_none()
    
    assert task_in_db is not None, "La tarea no se persistió."
    assert task_in_db.session_id == response.session_id, "Fallo en la vinculación de la sesión vivificada."
    assert task_in_db.status.value == "PENDING", "El estado inicial no es el correcto."
    assert task_in_db.payload["task_type"] == "extract_entities", "El type no se inyectó en el JSONB."

@pytest.mark.asyncio
async def test_task_manager_explicit_session(db_session):
    """
    Test de lógica core: Valida que el mánager reutiliza la sesión 
    existente y no crea duplicados en la base de datos.
    """
    task_repo = TaskRepository(db_session)
    session_repo = SessionRepository(db_session)
    entity_repo = EntityRepository(db_session)
    manager = TaskManager(db_session, task_repo, session_repo, entity_repo)

    # Forzamos la creación de una entidad y sesión previas
    mock_entity = await entity_repo.create(role="test_user")
    mock_session = await session_repo.create(entity_id=mock_entity.id)
    await db_session.flush()

    request = TaskDispatchRequest(
        task_type="follow_up",
        task_payload={"note": "Segunda iteración."},
        session_id=mock_session.id
    )

    response = await manager.dispatch_task(request)

    # Validamos que no se ha creado una sesión nueva
    assert response.session_id == mock_session.id, "El mánager ignoró el session_id explícito."