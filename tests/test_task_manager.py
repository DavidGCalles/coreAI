import pytest
from sqlalchemy import select, func

from src.db.database import AsyncSessionLocal, engine
from src.db.models import Task, Session, Entity
from src.repositories.relational import TaskRepository, SessionRepository
from src.managers.task_manager import TaskManager
from src.schemas.tasks import TaskDispatchRequest

@pytest.fixture
async def db_session():
    """Sesión transaccional limpia."""
    await engine.dispose()
    async with AsyncSessionLocal() as session:
        yield session
        await session.rollback()

def _manager(db_session):
    """
    Manager con los repos que sigue necesitando. El entity_repo quedó fuera
    en el ADR-012-002 (#113): la identidad la resuelve la FRONTERA contra el
    registro del despliegue y el manager jamás crea entidades.
    """
    return TaskManager(
        db_session,
        TaskRepository(db_session),
        SessionRepository(db_session),
    )

async def _nueva_entidad(db_session):
    """Entidad de pruebas sin slug: el manager NO resuelve identidad (la
    frontera ya lo hizo, ADR-012-002) — solo necesita una entidad resuelta."""
    entity = Entity(role="user")
    db_session.add(entity)
    await db_session.flush()
    return entity

@pytest.mark.asyncio
async def test_task_manager_vivifica_sesion_ligada_a_la_entidad_resuelta(db_session):
    """
    [ADR-012-002] #113: sin session_id, el manager vivifica la sesión
    huérfana LIGADA a la entidad resuelta y la task nace titulada. La
    identidad llega YA resuelta y NINGÚN despacho crea entidades: el límite
    exacto de la auto-vivificación (sesiones sí, entidades JAMÁS).
    """
    entities_antes = (await db_session.execute(select(func.count(Entity.id)))).scalar_one()
    resolved = await _nueva_entidad(db_session)

    request = TaskDispatchRequest(
        task_type="extract_entities",
        task_payload={"raw_text": "Cargando volcado mental."}
    )

    response = await _manager(db_session).dispatch_task(
        request, entity_id=resolved.id, actor="despachante-de-test"
    )

    # Contrato de salida: la entity_id resuelta viaja SIEMPRE (nunca silenciosa)
    assert response.entity_id == resolved.id
    # [ADR-012-003] #114: el actor estampado también viaja SIEMPRE
    assert response.actor == "despachante-de-test"

    result = await db_session.execute(select(Task).where(Task.id == response.task_id))
    task_in_db = result.scalar_one_or_none()

    assert task_in_db is not None, "La tarea no se persistió."
    assert task_in_db.session_id == response.session_id, "Fallo en la vinculación de la sesión vivificada."
    assert task_in_db.entity_id == resolved.id, "La tarea no nació titulada al despachante."
    assert task_in_db.status.value == "PENDING", "El estado inicial no es el correcto."
    assert task_in_db.payload["task_type"] == "extract_entities", "El type no se inyectó en el JSONB."
    # El actor vive en la COLUMNA de primer nivel (re-home #115): JAMÁS
    # en el JSONB del payload
    assert task_in_db.actor == "despachante-de-test"
    assert "actor" not in task_in_db.payload, "El actor coló en el JSONB: el re-home no se aplicó."

    session_in_db = (await db_session.execute(
        select(Session).where(Session.id == response.session_id)
    )).scalar_one()
    assert session_in_db.entity_id == resolved.id, "La sesión huérfana no quedó ligada a la entidad resuelta."

    # Fin de la auto-vivificación: ninguna entidad nació del despacho
    entities_despues = (await db_session.execute(select(func.count(Entity.id)))).scalar_one()
    assert entities_despues == entities_antes + 1, "El despacho auto-vivificó una entidad."

@pytest.mark.asyncio
async def test_task_manager_sesion_explicita_no_duplica_y_titula_al_despachante(db_session):
    """
    Con session_id explícito el manager no crea sesión y la task nace
    titulada a la entidad resuelta — que puede ser DISTINTA de la dueña de
    la sesión: la titularidad es del despacho, no de la sesión (ADR-012-002).
    """
    owner = await _nueva_entidad(db_session)
    despachante = await _nueva_entidad(db_session)
    assert str(owner.id) != str(despachante.id), "sanity: owner y despachante son entidades distintas."
    sesion_previa = await SessionRepository(db_session).create(entity_id=owner.id)
    await db_session.flush()

    sesiones_antes = (await db_session.execute(select(func.count(Session.id)))).scalar_one()

    request = TaskDispatchRequest(
        task_type="follow_up",
        task_payload={"note": "Segunda iteración."},
        session_id=sesion_previa.id
    )

    response = await _manager(db_session).dispatch_task(
        request, entity_id=despachante.id, actor="coreai-cron"
    )

    assert response.session_id == sesion_previa.id, "El manager ignoró el session_id explícito."
    assert response.actor == "coreai-cron"
    sesiones_despues = (await db_session.execute(select(func.count(Session.id)))).scalar_one()
    assert sesiones_despues == sesiones_antes, "El manager duplicó la sesión."

    task_in_db = (await db_session.execute(
        select(Task).where(Task.id == response.task_id)
    )).scalar_one()
    assert task_in_db.entity_id == despachante.id, "La task no quedó titulada al despachante."
    assert task_in_db.actor == "coreai-cron"

@pytest.mark.asyncio
async def test_task_manager_estampa_el_actor_por_omision_resuelto_por_la_frontera(db_session):
    """
    [ADR-012-003] #114: el manager NO resuelve el default — estampa lo que
    la frontera le pasa. Sin actor declarado, la frontera pasa el SLUG de la
    entidad resuelta (decisión de David, 25 nov: legible en Grafana y
    coherente con el namespace free-string; el DoD del issue decía
    'entity_id' y quedó des-actualizado al respecto, documentado en la PR).
    El manager solo verifica que ese acuerdo viaja íntegro.
    """
    resolved = await _nueva_entidad(db_session)

    request = TaskDispatchRequest(
        task_type="extract_entities",
        task_payload={"raw_text": "Despacho sin actor explícito."}
    )

    # Lo que la frontera pasaría: default = slug de la entidad resuelta
    response = await _manager(db_session).dispatch_task(
        request, entity_id=resolved.id, actor="system_default"
    )

    assert response.actor == "system_default"
    task_in_db = (await db_session.execute(
        select(Task).where(Task.id == response.task_id)
    )).scalar_one()
    assert task_in_db.actor == "system_default"
    # El actor jamás muta el payload del contrato: columna de primer nivel
    assert task_in_db.payload["task_type"] == "extract_entities"
    assert task_in_db.payload["raw_text"] == "Despacho sin actor explícito."
