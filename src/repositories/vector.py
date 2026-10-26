import uuid
import logging
from typing import Any
from qdrant_client import AsyncQdrantClient, models
from src.schemas.memory import DomainType, MemorySearchFilters

logger = logging.getLogger(__name__)

# =====================================================================
# COLECCIÓN DE IMÁGENES (ADR-007-002, Issue #50)
# =====================================================================
# Espacio propio para los embeddings de visión (jina-clip-v2), separado de
# las colecciones de texto: otra dimensión (1024 vs 384), otro significado.
# 1024 dims y coseno — exactamente la configuración validada en el E2E del
# handler de imágenes (#61): hasta ahora la colección se creaba A MANO.
IMAGE_COLLECTION = "images"
IMAGE_VECTOR_SIZE = 1024
IMAGE_VECTOR_DISTANCE = models.Distance.COSINE


class VectorRepository:
    """
    Repositorio vectorial mudo.
    Solo entiende de colecciones (dominios), UUIDs pre-generados, matrices de floats y JSONs.
    """
    def __init__(self, client: AsyncQdrantClient):
        self.client = client

    async def ensure_collection(
        self,
        collection_name: str,
        vector_size: int,
        distance: models.Distance,
    ) -> None:
        """
        Garantía idempotente: crea la colección si no existe, no la toca si ya existe.

        Idempotencia por comparación de nombres (get_collections), no por
        try/except: el arranque número uno y el reinicio número cien hacen
        exactamente lo mismo. El worker la invoca al arrancar (ADR-007-002
        #50) — un despliegue limpio ya no depende de que nadie cree la
        colección a mano.
        """
        existentes = {c.name for c in (await self.client.get_collections()).collections}
        if collection_name in existentes:
            logger.debug("Colección '%s' ya existe — nada que hacer", collection_name)
            return

        await self.client.create_collection(
            collection_name=collection_name,
            vectors_config=models.VectorParams(
                size=vector_size,
                distance=distance,
            ),
        )
        logger.info(
            "Colección '%s' creada (%d dims, %s)",
            collection_name, vector_size, distance,
        )

    async def collection_names(self) -> set[str]:
        """
        Nombres de las colecciones existentes en el vector store.
        El manager decide qué hacer con la ausencia — el repositorio no
        inventa política: solo reporta lo que hay.
        """
        return {
            c.name for c in (await self.client.get_collections()).collections
        }

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

        # Proyecto (FR-3, #51): clave plana del payload, simétrica en los
        # puntos de texto y de imagen — el filtro del recall la comparte.
        if filters.project is not None:
            must_conditions.append(
                models.FieldCondition(
                    key="project",
                    match=models.MatchValue(value=filters.project)
                )
            )

        if filters.tags_all:
            for tag in filters.tags_all:
                must_conditions.append(
                    models.FieldCondition(key="metadata.tags", match=models.MatchValue(value=tag))
                )
                
        if filters.tags_any:
            for tag in filters.tags_any:
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

    async def _upsert_point(
        self,
        collection: str,
        point_id: str,
        vector: list[float],
        payload: dict[str, Any],
    ) -> str:
        """Núcleo mudo del upsert: colección, punto y nada de semántica."""
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
        return await self._upsert_point(
            collection=domain.value,
            point_id=str(entity_id),
            vector=vector,
            payload=payload,
        )

    async def upsert_image(
        self,
        point_id: uuid.UUID,
        vector: list[float],
        payload: dict[str, Any],
    ) -> str:
        """
        Inyecta o actualiza un vector de IMAGEN en la colección de imágenes
        (ADR-007-002 #50: dim 1024 jina-clip-v2, espacio propio). El punto ES
        la tarea de imagen (idempotencia por re-dispatch); su payload ancla
        el vector a la SESIÓN del evento (correlación texto↔imagen, decisión
        22 oct) y a su proyecto (FR-3 #51). La imagen en sí NO se persiste:
        el vector es dato derivado.
        """
        return await self._upsert_point(
            collection=IMAGE_COLLECTION,
            point_id=str(point_id),
            vector=vector,
            payload=payload,
        )

    async def search(
        self,
        domain: "DomainType | str",
        query_vector: list[float],
        filters: MemorySearchFilters
    ) -> list[models.ScoredPoint]:
        """
        Búsqueda por similitud adaptada a la v1.19.0 (query_points).
        Devuelve los puntos crudos; la deserialización es problema de una capa superior.

        Acepta un DomainType (colección de texto por dominio) o el nombre
        plano de una colección (p. ej. la de imágenes): el repositorio es
        mudo y no conoce jerarquías de dominio.
        """
        collection = domain.value if isinstance(domain, DomainType) else str(domain)
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
                wait=True,
            )
        except Exception as e:
            logger.error("Fallo al eliminar vector %s: %s", entity_id, e)
            raise
