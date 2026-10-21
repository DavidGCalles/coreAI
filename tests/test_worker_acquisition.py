import pytest
import uuid
from sqlalchemy import select, delete
from src.db.database import AsyncSessionLocal, engine
from src.db.models import Task
from src.repositories.relational import TaskRepository, SessionRepository, EntityRepository

@pytest.fixture
async def db_session():
    """
    Sesión limpia, blindada y que RECOGE SU BASURA.
    """
    await engine.dispose()
    session = AsyncSessionLocal()
    try:
        # Limpieza previa por si otro test falló catastróficamente
        await session.execute(delete(Task))
        await session.commit()
        
        yield session
    finally:
        # Tierra quemada al salir. Cero dummies en tu base de datos de desarrollo.
        await session.execute(delete(Task))
        await session.commit()
        
        await session.close()
        await engine.dispose() 

@pytest.fixture
async def setup_pending_task(db_session):
    """Genera el árbol relacional mínimo y una tarea PENDING."""
    entity_repo = EntityRepository(db_session)
    session_repo = SessionRepository(db_session)
    task_repo = TaskRepository(db_session)

    entity = await entity_repo.create(role="test_worker")
    session_db = await session_repo.create(entity_id=entity.id)
    
    task = await task_repo.create(
        session_id=session_db.id,
        payload={"task_type": "dummy_test_task"}
    )
    await db_session.commit()
    
    return task.id

@pytest.mark.asyncio
async def test_worker_claim_and_complete_lifecycle(db_session, setup_pending_task):
    """Valida que una tarea se bloquea, transita a RUNNING y finaliza en COMPLETED."""
    repo = TaskRepository(db_session)

    # 1. Fase de Adquisición (Claim)
    claimed_task = await repo.claim_next_task()
    assert claimed_task is not None, "El worker no encontró la tarea PENDING."
    
    claimed_task_id = claimed_task.id
    claimed_status = claimed_task.status.value

    # Ahora sí, tenemos garantías de que reclamamos la que acabamos de crear
    assert claimed_task_id == setup_pending_task, "El ID de la tarea reclamada no coincide. ¡Hay zombis en la BD!"
    assert claimed_status == "RUNNING", "La tarea no transitó a RUNNING tras ser reclamada."

    # 2. Validación de Cola Vacía (El SKIP LOCKED funciona)
    empty_task = await repo.claim_next_task()
    assert empty_task is None, "El worker reclamó una tarea que ya estaba en RUNNING."

    # 3. Fase de Éxito (Complete)
    await repo.complete_task(claimed_task_id)

    # 4. Verificación final en Base de Datos
    db_session.expunge_all() 
    result = await db_session.execute(select(Task).where(Task.id == setup_pending_task))
    final_task = result.scalar_one()
    
    assert final_task.status.value == "COMPLETED", "La tarea no se consolidó como COMPLETED."

@pytest.mark.asyncio
async def test_worker_claim_and_fail_lifecycle(db_session, setup_pending_task):
    """Valida la inyección del error en el JSONB cuando la tarea falla."""
    repo = TaskRepository(db_session)

    # 1. Fase de Adquisición (Claim)
    claimed_task = await repo.claim_next_task()
    
    # Assert de seguridad añadido también aquí
    assert claimed_task.id == setup_pending_task, "El ID no coincide. Fantasmas en la BD."
    
    claimed_task_id = claimed_task.id 

    # 2. Fase de Fallo (Fail)
    error_message = "Explosión termonuclear en el Córtex"
    await repo.fail_task(claimed_task_id, error_message)

    # 3. Verificación final en Base de Datos
    db_session.expunge_all()
    result = await db_session.execute(select(Task).where(Task.id == setup_pending_task))
    final_task = result.scalar_one()
    
    assert final_task.status.value == "FAILED", "La tarea no transitó a FAILED."
    assert final_task.payload.get("error_trace") == error_message, "El JSONB no capturó la traza del error."