# src/db/vector_db.py
import logging

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models

from src.db.entities_config import SYSTEM_DEFAULT_SLUG
from src.managers.config_manager import config_manager
from src.repositories.vector import (
    VectorRepository,
    IMAGE_COLLECTION,
    IMAGE_VECTOR_SIZE,
    IMAGE_VECTOR_DISTANCE,
    REGISTERED_COLLECTIONS,
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


async def ensure_owners() -> None:
    """
    Backfill idempotente de titularidad (D1 del ADR-012, ejecutado en el
    [ADR-012-005] #116): set_payload dirigido por filtro sobre TODAS las
    colecciones registradas — toca SOLO los puntos cuya clave plana 'owner'
    está vacía o ausente y les estampa el slug `system_default`.

    Regla de cierre de históricos (decisión de David, 25 nov): nadie pierde
    acceso al subir la familia. Los puntos pre-familia no llevaban owner (el
    #115 empezó a estamparlo a la escritura); sin este cierre quedarían para
    siempre fuera del filtro de titularidad y un recall con owner jamás los
    vería — el histórico queda a nombre del inquilino por defecto del
    sustrato, la misma identidad que la frontera resuelve para un despacho
    omiso ([ADR-012-002] #113).

    Idempotente por construcción (gemelo ensure_* del ADR-011 aplicado al
    payload vectorial): el selector (is_empty O is_null sobre 'owner')
    excluye a todo punto con titularidad ya estampada — N arranques
    convergen al mismo estado y JAMÁS se sobrescribe un owner existente.
    La garantía vive en el arranque, junto a ensure_collections: cada
    proceso garantiza el sustrato que va a tocar (BUG-011 #107), y la
    regla queda auditable en git.

    La raza de arranque paralelo (worker y core-mcp a la vez) es benigna:
    ambos estampan el MISMO slug con el mismo selector — el set converge.
    """
    client = await get_qdrant_client()
    selector = models.FilterSelector(
        filter=models.Filter(
            should=[
                models.IsEmptyCondition(is_empty=models.PayloadField(key="owner")),
                models.IsNullCondition(is_null=models.PayloadField(key="owner")),
            ],
        )
    )
    for collection in REGISTERED_COLLECTIONS:
        try:
            await client.set_payload(
                collection_name=collection,
                payload={"owner": SYSTEM_DEFAULT_SLUG},
                points=selector,
                wait=True,
            )
            logger.info("Backfill de titularidad garantizado: '%s'", collection)
        except Exception as e:
            logger.error(
                "Fallo estructural en el backfill de titularidad de '%s': %s",
                collection, e,
            )
            raise
