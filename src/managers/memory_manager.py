import os
import logging
import time
import asyncio
from openai import AsyncOpenAI
from qdrant_client import AsyncQdrantClient, models
from src.schemas.memory import CoreMemoryNode, MemorySearchFilters, DomainType

from src.logging_config import configure_logging
configure_logging(level=logging.INFO)
logger = logging.getLogger(__name__)

EMBEDDING_DIMENSION = 384  # Configurado para multilingual-e5-small o similar vía Infinity

class VectorMemoryManager:
    """
    Motor vectorial agnóstico para CoreAI.
    Gestiona la persistencia y búsqueda semántica aislada por multitenencia.
    """

    def __init__(self):
        self._client = None
        self._collections = [domain.value for domain in DomainType]

    async def _initialize_client(self):
        from src.managers.config_manager import config_manager
        
        if self._client is not None:
            return

        q_config = config_manager.get_qdrant_config()
        host = q_config["url"]
        api_key = q_config.get("api_key")

        try:
            if api_key:
                self._client = AsyncQdrantClient(url=host, api_key=api_key)
            else:
                url = host if "://" in host else f"http://{host}:6333"
                self._client = AsyncQdrantClient(url=url)
        except Exception as e:
            logger.error("Fallo crítico al conectar con Qdrant: %s", e, exc_info=True)
            raise ConnectionError("Fallo crítico al conectar con Qdrant: %s", e)

        await self._ensure_all_collections()

    async def _ensure_collection(self, collection_name: str) -> None:
        try:
            await self._client.get_collection(collection_name=collection_name)
        except Exception:
            logger.info("Colección '%s' no encontrada. Inicializando...", collection_name)
            await self._client.create_collection(
                collection_name=collection_name,
                vectors_config=models.VectorParams(
                    size=EMBEDDING_DIMENSION,
                    distance=models.Distance.COSINE,
                ),
            )

        # Índices estratégicos para el filtrado estricto (RBAC y Tags)
        payload_indexes = ["tenant_id", "visibility", "metadata.tags", "metadata.source_id"]
        
        for field in payload_indexes:
            try:
                await self._client.create_payload_index(
                    collection_name=collection_name,
                    field_name=field,
                    field_schema=models.PayloadSchemaType.KEYWORD,
                    wait=False,
                )
            except Exception as e:
                logger.debug("Índice ya existente o error menor en '%s': %s", field, e)

    async def _ensure_all_collections(self) -> None:
        for coll in self._collections:
            await self._ensure_collection(coll)

    async def _get_embedding(self, text: str) -> list[float]:
        """
        Delega la generación al proxy unificado de LiteLLM.
        El enrutamiento real hacia Infinity se resuelve en la capa de red del Docker Compose.
        """
        litellm_url = os.getenv("LITELLM_URL", "http://localhost:4000")
        if not litellm_url.startswith("http"):
            litellm_url = f"http://{litellm_url}"
            
        client = AsyncOpenAI(
            base_url=f"{litellm_url.rstrip('/')}/v1",
            api_key="sk-coreai-internal"
        )
        
        try:
            response = await client.embeddings.create(
                model="text-embedding",
                input=[text]
            )
            return response.data[0].embedding
        except Exception as e:
            logger.error("Fallo en proxy de embeddings: %s", str(e), exc_info=True)
            raise

    def _build_qdrant_filter(self, filters: MemorySearchFilters) -> models.Filter:
        """Construye un árbol de condiciones strictas en Qdrant desde el Pydantic schema."""
        must_conditions = [
            models.FieldCondition(
                key="tenant_id", 
                match=models.MatchValue(value=filters.tenant_id)
            )
        ]

        if filters.tags_all:
            for tag in filters.tags_all:
                must_conditions.append(
                    models.FieldCondition(key="metadata.tags", match=models.MatchValue(value=tag))
                )
        
        if filters.tags_any:
            must_conditions.append(
                models.FieldCondition(key="metadata.tags", match=models.MatchAny(any=filters.tags_any))
            )
            
        if filters.properties_match:
            for key, value in filters.properties_match.items():
                must_conditions.append(
                    models.FieldCondition(
                        key=f"metadata.properties.{key}", 
                        match=models.MatchValue(value=value)
                    )
                )

        return models.Filter(must=must_conditions)

    async def add_memory(self, node: CoreMemoryNode) -> str:
        """
        Ingesta atómica. Serializa el nodo y lo inyecta en su dominio correspondiente.
        """
        await self._initialize_client()
        
        vector = await self._get_embedding(node.content)
        payload = node.model_dump(mode="json")
        collection = node.domain.value

        try:
            await self._client.upsert(
                collection_name=collection,
                points=[
                    models.PointStruct(
                        id=str(node.id),
                        vector=vector,
                        payload=payload
                    )
                ],
                wait=True,
            )
            logger.info("Nodo %s insertado en %s para tenant %s", node.id, collection, node.tenant_id)
            return str(node.id)
        except Exception as e:
            logger.error("Fallo al insertar nodo %s: %s", node.id, e, exc_info=True)
            raise

    async def search_memory(self, query: str, filters: MemorySearchFilters) -> list[CoreMemoryNode]:
        """
        Búsqueda semántica condicional. Si no se provee dominio, realiza un scatter-gather
        sobre todas las colecciones y devuelve los N mejores resultados consolidados.
        """
        await self._initialize_client()
        query_vector = await self._get_embedding(query)
        qdrant_filter = self._build_qdrant_filter(filters)
        
        target_collections = [filters.domain.value] if filters.domain else self._collections
        
        async def _search_collection(collection_name: str):
            try:
                return await self._client.search(
                    collection_name=collection_name,
                    query_vector=query_vector,
                    query_filter=qdrant_filter,
                    limit=filters.limit,
                    score_threshold=filters.score_threshold
                )
            except Exception as e:
                logger.warning("Error buscando en %s: %s", collection_name, e)
                return []

        # Búsqueda concurrente si hay múltiples colecciones
        results = await asyncio.gather(*[_search_collection(c) for c in target_collections])
        
        # Aplanar, ordenar por score descendente y truncar al límite global
        all_points = [point for sublist in results for point in sublist]
        all_points.sort(key=lambda x: x.score, reverse=True)
        top_points = all_points[:filters.limit]

        parsed_nodes = []
        for point in top_points:
            try:
                parsed_nodes.append(CoreMemoryNode.model_validate(point.payload))
            except Exception as e:
                logger.error("Integridad comprometida en nodo %s: %s", point.id, e)
                continue

        return parsed_nodes

    async def delete_memory(self, memory_id: str, domain: DomainType | None = None):
        """
        Borrado por ID. Si no se especifica dominio, barre todas las colecciones.
        """
        await self._initialize_client()
        target_collections = [domain.value] if domain else self._collections
        
        for collection in target_collections:
            try:
                await self._client.delete(
                    collection_name=collection,
                    points_selector=models.PointIdsList(points=[str(memory_id)]),
                )
            except Exception as e:
                logger.error("Error borrando %s de %s: %s", memory_id, collection, e, exc_info=True)
                
    async def get_collection_info(self, collection_name: str) -> dict:
        """Obtiene estadísticas reales de la colección en Qdrant."""
        await self._initialize_client()
        try:
            collection_info = await self._client.get_collection(collection_name=collection_name)
            return {
                "status": collection_info.status,
                "vectors_count": collection_info.vectors_count,
                "config": {
                    "vector_size": collection_info.config.params.vectors.size,
                    "distance": collection_info.config.params.vectors.distance.value
                }
            }
        except Exception as e:
            logger.error(f"Error al obtener info de {collection_name}: {e}")
            return {"error": str(e)}