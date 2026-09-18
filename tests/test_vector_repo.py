import pytest
import uuid
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qmodels
from src.managers.config_manager import config_manager
from src.repositories.vector import VectorRepository
from src.schemas.memory import DomainType, MemorySearchFilters

@pytest.fixture
async def vector_setup():
    """Fixture para preparar el cliente, el repo y asegurar la colección."""
    q_config = config_manager.get_qdrant_config()
    client = AsyncQdrantClient(url=q_config["url"], api_key=q_config.get("api_key"))
    repo = VectorRepository(client)
    
    # Usaremos el dominio SYSTEM como campo de pruebas
    test_domain = DomainType.SYSTEM
    
    # Nos aseguramos de que la colección existe y admite nuestro vector dummy de 4 dimensiones
    try:
        await client.get_collection(collection_name=test_domain.value)
    except Exception:
        await client.create_collection(
            collection_name=test_domain.value,
            vectors_config=qmodels.VectorParams(
                size=384,
                distance=qmodels.Distance.COSINE
            )
        )
        
    yield repo, test_domain
    
    # Cerramos conexiones al salir
    await client.close()


async def test_vector_repository_lifecycle(vector_setup):
    """Valida la inyección, el filtrado estricto (v1.19.0) y el borrado atómico."""
    repo, domain = vector_setup
    
    test_uuid = uuid.uuid4()
    test_vector = [0.1] * 384
    test_tenant = f"tenant_test_{uuid.uuid4()}"
    
    payload = {
        "tenant_id": test_tenant,
        "metadata": {
            "tags": ["pytest", "coreai"],
            "properties": {"is_test": True}
        }
    }
    
    # 1. UPSERT
    inserted_id = await repo.upsert(
        domain=domain, 
        entity_id=test_uuid, 
        vector=test_vector, 
        payload=payload
    )
    assert inserted_id == str(test_uuid), "El ID devuelto no coincide con el inyectado."

    # 2. SEARCH (Validando la traducción de filtros Pydantic -> Qdrant)
    filters = MemorySearchFilters(
        tenant_id=test_tenant,
        query="dummy_text",  # El repo lo ignora, usa el query_vector
        tags_all=["pytest"]
    )
    
    results = await repo.search(
        domain=domain, 
        query_vector=test_vector, 
        filters=filters
    )
    
    assert len(results) == 1, "El filtro de búsqueda falló o no encontró el vector."
    assert results[0].id == str(test_uuid), "Corrupción en el ID recuperado."
    assert results[0].payload["metadata"]["properties"]["is_test"] is True, "Pérdida de propiedades en el payload."

    # 3. SEARCH NEGATIVO (Asegurando que el multitenant bloquea intrusos)
    bad_filters = MemorySearchFilters(
        tenant_id="intruder_tenant",
        query="dummy_text"
    )
    empty_results = await repo.search(
        domain=domain, 
        query_vector=test_vector, 
        filters=bad_filters
    )
    assert len(empty_results) == 0, "Brecha de seguridad: El filtro tenant_id no está aislando los datos."

    # 4. DELETE
    await repo.delete(domain=domain, entity_id=test_uuid)
    
    # 5. VERIFICACIÓN DE TIERRA QUEMADA
    deleted_results = await repo.search(
        domain=domain, 
        query_vector=test_vector, 
        filters=filters
    )
    assert len(deleted_results) == 0, "El vector fantasma sigue existiendo tras el borrado."