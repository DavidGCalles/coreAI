import pytest
import uuid
from sqlalchemy import select
from src.db.database import AsyncSessionLocal
from src.db.models import Entity
import uuid
import pytest
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qmodels
from src.managers.config_manager import config_manager


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

@pytest.mark.asyncio
async def test_qdrant_connection_and_uuid_integrity():
    """Valida la conexión asíncrona a Qdrant y la compatibilidad estricta con UUID v4."""
    
    # 1. Configuración y Cliente Crudo
    q_config = config_manager.get_qdrant_config()
    host = q_config["url"]
    api_key = q_config.get("api_key")
         
    client = AsyncQdrantClient(url=host, api_key=api_key)
    
    test_collection = "test_ephemeral_core"
    test_uuid = str(uuid.uuid4())
    # Dimensión ficticia para el test (ej. 4 dimensiones)
    test_vector = [0.1, 0.2, 0.3, 0.4] 
    
    try:
        # 2. Inicialización de Colección Efímera
        await client.create_collection(
            collection_name=test_collection,
            vectors_config=qmodels.VectorParams(
                size=len(test_vector),
                distance=qmodels.Distance.COSINE
            )
        )
        
        # 3. Inserción con UUID de Python
        await client.upsert(
            collection_name=test_collection,
            points=[
                qmodels.PointStruct(
                    id=test_uuid,
                    vector=test_vector,
                    payload={"source": "pytest", "status": "active"}
                )
            ]
        )
        
        # 4. Recuperación y Validación (API v1.19.0+)
        search_result = await client.query_points(
            collection_name=test_collection,
            query=test_vector,
            limit=1
        )
        
        assert len(search_result.points) > 0, "No se recuperaron resultados de Qdrant."
        assert search_result.points[0].id == test_uuid, "Corrupción de UUID en el motor vectorial."
        assert search_result.points[0].payload["source"] == "pytest", "Pérdida de payload en la inserción."
        
    finally:
        # 5. Tierra Quemada (Limpieza obligatoria incluso si falla el assert)
        try:
            await client.delete_collection(collection_name=test_collection)
            await client.close()
        except Exception:
            pass # Si falla al borrar, el contenedor se ensuciará, pero el test original ya reportó el fallo.