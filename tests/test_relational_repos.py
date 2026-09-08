import pytest
import uuid
from sqlalchemy.ext.asyncio import AsyncSession
# Importamos también el engine global
from src.db.database import AsyncSessionLocal, engine 
from src.db.models import Entity
from src.repositories.relational import EntityRepository

@pytest.fixture
async def db_session():
    """Fixture que provee una sesión asíncrona aislada para los tests."""
    
    # 1. Fulminamos el pool heredado del import para que se ate al bucle de este test
    await engine.dispose() 
    
    # 2. Ahora sí, abrimos la sesión limpia
    async with AsyncSessionLocal() as session:
        yield session
        # 3. Limpieza de sangre
        await session.rollback()

@pytest.mark.asyncio
async def test_entity_repository_crud(db_session: AsyncSession):
    """Valida el ciclo de vida completo de un modelo a través del repositorio base."""
    repo = EntityRepository(db_session)
    
    # 1. CREATE (Y validación del flush)
    test_payload = {"source": "pytest", "is_test": True}
    new_entity = await repo.create(role="test_worker", metadata_payload=test_payload)
    
    # El repositorio solo hace flush, pero eso ya debería haberle asignado un UUID desde Postgres
    assert new_entity.id is not None, "El flush() no está generando el UUID primario."
    assert isinstance(new_entity.id, uuid.UUID), "El ID generado no es un tipo UUID nativo."
    assert new_entity.role == "test_worker"
    
    # Hacemos commit explícito para asentar la transacción en disco
    await db_session.commit()
    entity_id = new_entity.id
    
    # 2. GET
    fetched_entity = await repo.get(entity_id)
    assert fetched_entity is not None, "Fallo al recuperar la entidad por UUID."
    assert fetched_entity.metadata_payload["source"] == "pytest"
    
    # 3. UPDATE
    updated_entity = await repo.update(fetched_entity, role="upgraded_worker")
    assert updated_entity.role == "upgraded_worker", "El atributo no se actualizó en el objeto."
    await db_session.commit()
    
    # Verificamos que la actualización persistió
    re_fetched = await repo.get(entity_id)
    assert re_fetched.role == "upgraded_worker", "La actualización no se volcó a la base de datos."
    
    # 4. DELETE
    await repo.delete(re_fetched)
    await db_session.commit()
    
    # Validamos tierra quemada
    deleted_entity = await repo.get(entity_id)
    assert deleted_entity is None, "La entidad sigue existiendo tras el delete()."