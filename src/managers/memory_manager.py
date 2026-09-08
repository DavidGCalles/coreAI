import logging
import uuid
from typing import Any, Sequence
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.schemas.memory import DomainType, MemorySearchFilters, Visibility
from src.repositories.relational import EventRepository
from src.repositories.vector import VectorRepository
from src.db.models import Event

logger = logging.getLogger(__name__)

class HybridMemoryManager:
    """
    Orquestador Transaccional.
    Coordina la inyección dual (Postgres + Qdrant) y garantiza la consistencia (Rollback).
    No contiene lógica directa de bases de datos.
    """
    def __init__(
        self, 
        session: AsyncSession, 
        relational_repo: EventRepository, 
        vector_repo: VectorRepository
    ):
        self.session = session
        self.relational_repo = relational_repo
        self.vector_repo = vector_repo

    async def _get_embedding(self, text: str) -> list[float]:
        """
        Punto de aislamiento para la llamada a LiteLLM.
        Se implementará en el Epic 3. Por ahora, levanta una excepción si no se mockea.
        """
        raise NotImplementedError("Llamada al LLM Proxy no implementada (Requiere Epic 3).")

    async def add_memory(
        self, 
        entity_id: uuid.UUID, 
        domain: DomainType, 
        content: str, 
        visibility: Visibility = Visibility.PRIVATE,
        metadata: dict[str, Any] = None
    ) -> uuid.UUID:
        """
        Flujo de Ingesta Transaccional:
        1. Crea en Postgres (obtiene UUID temporal vía flush).
        2. Genera Embedding.
        3. Inyecta en Qdrant.
        4. Si todo va bien, Commit. Si Qdrant falla, Rollback.
        """
        metadata = metadata or {}
        
        try:
            # 1. Inserción Relacional (Flush para generar UUID, SIN commit)
            new_event = await self.relational_repo.create(
                entity_id=entity_id,
                content=content,
                domain=domain,
                visibility=visibility
            )
            
            # 2. IA: Generación de Vector
            vector = await self._get_embedding(content)
            
            # 3. Inserción Vectorial (Qdrant)
            # El tenant_id lo podemos derivar del entity_id para mantener el aislamiento
            payload = {
                "tenant_id": str(entity_id),
                "content": content,
                "metadata": metadata
            }
            
            await self.vector_repo.upsert(
                domain=domain,
                entity_id=new_event.id,
                vector=vector,
                payload=payload
            )
            
            # 4. Consistencia: Asentamos la transacción
            await self.session.commit()
            logger.info("Memoria híbrida consolidada con éxito: %s", new_event.id)
            return new_event.id
            
        except Exception as e:
            # 5. Cortafuegos: Si Qdrant (o el LLM) revienta, abortamos Postgres
            await self.session.rollback()
            logger.error("Fallo en la inyección de memoria, transacción abortada: %s", e)
            raise

    async def search_memory(self, entity_id: uuid.UUID, query: str, filters: MemorySearchFilters) -> Sequence[Event]:
        """
        Patrón Scatter-Gather:
        1. Busca similitud en Qdrant (obtiene UUIDs).
        2. Hidrata los objetos completos desde Postgres.
        """
        # 1. IA: Vectorizamos la pregunta
        query_vector = await self._get_embedding(query)
        
        # 2. Búsqueda Vectorial (Devuelve puntos con score e ID)
        domain = filters.domain or DomainType.SYSTEM
        scored_points = await self.vector_repo.search(
            domain=domain,
            query_vector=query_vector,
            filters=filters
        )
        
        if not scored_points:
            return []
            
        # Extraemos los UUIDs crudos y los casteamos para Postgres
        memory_ids = [uuid.UUID(point.id) for point in scored_points]
        
        # 3. Hidratación Relacional (WHERE id IN ...)
        stmt = select(Event).where(Event.id.in_(memory_ids))
        result = await self.session.execute(stmt)
        hydrated_events = result.scalars().all()
        
        # Nota: Postgres devuelve los IN desordenados. Si quieres mantener 
        # el orden semántico (por score), habría que reordenar `hydrated_events` 
        # basándote en la lista original `memory_ids`.
        
        return hydrated_events