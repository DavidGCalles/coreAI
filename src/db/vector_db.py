# src/db/vector_db.py
import logging
from qdrant_client import AsyncQdrantClient
from src.managers.config_manager import config_manager

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