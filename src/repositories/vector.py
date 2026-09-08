import uuid
import logging
from typing import Any
from qdrant_client import AsyncQdrantClient, models
from src.schemas.memory import DomainType, MemorySearchFilters

logger = logging.getLogger(__name__)

class VectorRepository:
    """
    Repositorio vectorial mudo.
    Solo entiende de colecciones (dominios), UUIDs pre-generados, matrices de floats y JSONs.
    """
    def __init__(self, client: AsyncQdrantClient):
        self.client = client

    def _build_filter(self, filters: MemorySearchFilters) -> models.Filter:
        """Traduce los filtros semánticos de Pydantic al dialecto duro de Qdrant."""
        must_conditions = []
        
        # Multitenencia obligatoria
        must_conditions.append(
            models.FieldCondition(
                key="tenant_id", 
                match=models.MatchValue(value=filters.tenant_id)
            )
        )

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

    async def upsert(
        self, 
        domain: DomainType, 
        entity_id: uuid.UUID, 
        vector: list[float], 
        payload: dict[str, Any]
    ) -> str:
        """
        Inyecta o actualiza un vector vinculándolo indisolublemente al UUID relacional.
        """
        collection = domain.value
        point_id = str(entity_id)

        try:
            await self.client.upsert(
                collection_name=collection,
                points=[
                    models.PointStruct(
                        id=point_id,
                        vector=vector,
                        payload=payload
                    )
                ],
                wait=True,
            )
            return point_id
        except Exception as e:
            logger.error("Fallo estructural al insertar vector %s en Qdrant: %s", point_id, e)
            raise

    async def search(
        self, 
        domain: DomainType, 
        query_vector: list[float], 
        filters: MemorySearchFilters
    ) -> list[models.ScoredPoint]:
        """
        Búsqueda por similitud adaptada a la v1.19.0 (query_points).
        Devuelve los puntos crudos; la deserialización es problema de una capa superior.
        """
        collection = domain.value
        qdrant_filter = self._build_filter(filters)

        try:
            # API moderna de Qdrant v1.19.0
            response = await self.client.query_points(
                collection_name=collection,
                query=query_vector,
                query_filter=qdrant_filter,
                limit=filters.limit,
                score_threshold=filters.score_threshold
            )
            return response.points
        except Exception as e:
            logger.error("Error consultando la colección %s: %s", collection, e)
            raise

    async def delete(self, domain: DomainType, entity_id: uuid.UUID) -> None:
        """Borrado quirúrgico por UUID."""
        collection = domain.value
        try:
            await self.client.delete(
                collection_name=collection,
                points_selector=models.PointIdsList(points=[str(entity_id)]),
                wait=True
            )
        except Exception as e:
            logger.error("Fallo al eliminar vector %s: %s", entity_id, e)
            raise