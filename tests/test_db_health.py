import pytest
import uuid
from sqlalchemy import select
from src.db.database import AsyncSessionLocal
from src.db.models import Entity

@pytest.mark.asyncio
async def test_database_connection_and_crud():
    """Valida la conexión asíncrona a PostgreSQL insertando y borrando una Entity."""
    
    test_entity_id = uuid.uuid4()
    
    async with AsyncSessionLocal() as session:
        # 1. Crear
        new_entity = Entity(
            id=test_entity_id, 
            role='system_test', 
            metadata_payload={'source': 'pytest', 'ephemeral': True}
        )
        session.add(new_entity)
        await session.commit()
        
        # 2. Leer
        result = await session.execute(select(Entity).where(Entity.id == test_entity_id))
        entity_db = result.scalar_one_or_none()
        
        assert entity_db is not None
        assert entity_db.id == test_entity_id
        assert entity_db.role == 'system_test'
        
        # 3. Borrar
        await session.delete(entity_db)
        await session.commit()
        
        # 4. Validar borrado
        result_deleted = await session.execute(select(Entity).where(Entity.id == test_entity_id))
        assert result_deleted.scalar_one_or_none() is None