# src/db/vector_db.py
import logging

from qdrant_client import AsyncQdrantClient

from src.managers.config_manager import config_manager
from src.repositories.vector import (
    VectorRepository,
    IMAGE_COLLECTION,
    IMAGE_VECTOR_SIZE,
    IMAGE_VECTOR_DISTANCE,
    TEXT_VECTOR_SIZE,
    TEXT_VECTOR_DISTANCE,
)
from src.schemas.memory import DomainType

logger = logging.getLogger(__name__)

# Singleton lazy-initialized
_qdrant_client: AsyncQdrantClient = None

async def get_qdrant_client() -> AsyncQdrantClient:
    """
    Obtiene o inicializa el cliente Qdrant global.
    Patrón singleton asíncrono para evitar múltiples conexiones concurrentes innecesarias.
    """
    global _qdrant_client
    
    if _qdrant_client is None:
        q_config = config_manager.get_qdrant_config()
        # Inicializamos el cliente. Recordamos NO usar prefer_grpc=True
        _qdrant_client = AsyncQdrantClient(
            url=q_config["url"],
            api_key=q_config.get("api_key")
        )
        logger.info("Cliente Qdrant global inicializado apuntando a: %s", q_config["url"])
    
    return _qdrant_client

async def close_qdrant_client() -> None:
    """
    Cierra y resetea el cliente singleton (BUG-011 #107).

    Gemelo del engine.dispose() del BUG-010 en el lado Qdrant: el bootstrap
    de core-mcp corre en un event loop efímero (asyncio.run en main) que se
    cierra antes de servir, y este cliente retiene estado del loop donde
    nace (el AsyncClient de httpx y sus pools quedan atados a su loop). Sin
    cierre+reset dentro del loop efímero, el serving reutilizaría un cliente
    nacido en un loop ya cerrado. Al resetear el singleton, el loop de
    serving lo recrea lazy en su primer uso.
    """
    global _qdrant_client

    if _qdrant_client is not None:
        await _qdrant_client.close()
        _qdrant_client = None
        logger.info("Cliente Qdrant global cerrado y singleton reseteado (BUG-011 #107)")


async def ensure_collections() -> None:
    """
    Garantía idempotente de TODAS las colecciones derivadas del sistema
    (ADR-007-002 #50; BUG-001 #34).

    La colección de imágenes se creó a mano en su día: nada garantizaba su
    existencia en un despliegue limpio, y el handler moría asíncrono contra
    una colección inexistente. Las colecciones de TEXTO por dominio
    (document, conversation, system — multilingual-e5-small, 384 dims,
    coseno) tenían el mismo agujero hasta el BUG-001: un `docker-compose
    down -v` seguido de arranque limpio las auto-genera TODAS sin
    intervención manual, y el arranque es idempotente (comparación de
    nombres, nunca try/except). Si una colección preexistente trae dims
    de otro modelo, el arranque revienta explícito — la topología se
    valida ANTES de aceptar tráfico, no tarea a tarea en el upsert.

    Vive junto al cliente que garantiza desde el BUG-011 (#107): la llama
    el worker antes de consumir y core-mcp junto al bootstrap relacional —
    cada proceso garantiza el sustrato que va a tocar, no el de su vecino.
    """
    client = await get_qdrant_client()
    repo = VectorRepository(client=client)

    # Colecciones de texto: una por cada dominio del catálogo (BUG-001 #34),
    # mismas dims que el embedding de texto que genera _get_embedding.
    for domain in DomainType:
        await repo.ensure_collection(
            collection_name=domain.value,
            vector_size=TEXT_VECTOR_SIZE,
            distance=TEXT_VECTOR_DISTANCE,
        )

    # Colección de imágenes (jina-clip-v2, 1024 dims — ADR-007-002 #50).
    await repo.ensure_collection(
        collection_name=IMAGE_COLLECTION,
        vector_size=IMAGE_VECTOR_SIZE,
        distance=IMAGE_VECTOR_DISTANCE,
    )
