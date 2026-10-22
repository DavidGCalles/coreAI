# src/db/vector_db.py
import logging
from qdrant_client import AsyncQdrantClient, models
from src.managers.config_manager import config_manager

logger = logging.getLogger(__name__)

# Singleton lazy-initialized
_qdrant_client: AsyncQdrantClient = None

# Colección de imágenes (ADR-007-002 #50): espacio propio, separado de las
# colecciones de texto. jina-clip-v2 sirve 1024 dimensiones — verificado en
# el E2E del #61: los puntos de imagen aterrizan con 1024 dims.
IMAGE_COLLECTION = "images"
IMAGE_VECTOR_SIZE = 1024

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

async def ensure_image_collection() -> bool:
    """
    Init idempotente de la colección de imágenes (ADR-007-002 #50).

    La colección la garantiza el worker AL ARRANQUE (decisión de David:
    «ensure collection al arranque del worker — lo más sencillo y atómico»):
    un despliegue limpio ya no depende de que nadie la cree a mano, y un
    wipe del volumen de Qdrant se repara solo con el siguiente arranque.

    - Ya existe → no-op (devuelve False).
    - No existe → se crea con dim 1024 y distancia COSINE (devuelve True).
    - Qdrant no alcanzable → el error PROPAGA: fail-fast al arranque. Un
      worker sin su almacén de vectores es un nodo degradado que descubriría
      la falta en plena tarea — mejor que no arranque.
    """
    client = await get_qdrant_client()

    try:
        await client.get_collection(collection_name=IMAGE_COLLECTION)
        logger.info("Colección '%s' ya existe — init idempotente no-op", IMAGE_COLLECTION)
        return False
    except Exception:
        logger.info(
            "Colección '%s' no encontrada (o Qdrant no alcanzable) — creando (%d dims, COSINE)",
            IMAGE_COLLECTION, IMAGE_VECTOR_SIZE,
        )

    await client.create_collection(
        collection_name=IMAGE_COLLECTION,
        vectors_config=models.VectorParams(
            size=IMAGE_VECTOR_SIZE,
            distance=models.Distance.COSINE,
        ),
    )
    return True
