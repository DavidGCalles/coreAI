import logging
import uuid
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.schemas.memory import DomainType, MemorySearchFilters, Visibility
from src.schemas.llm import EmbeddingRequest
from src.repositories.relational import EventRepository, LLMAuditRepository
from src.repositories.vector import VectorRepository
from src.managers.llm_client import LLMClient
from src.db.models import Event

logger = logging.getLogger(__name__)

class HybridMemoryManager:
    """
    Orquestador Transaccional.
    Coordina la inyección dual (Postgres + Qdrant) y garantiza la consistencia (Rollback).
    No contiene lógica directa de bases de datos ni validación HTTP.
    """
    def __init__(
        self, 
        session: AsyncSession, 
        relational_repo: EventRepository, 
        vector_repo: VectorRepository,
        audit_repo: LLMAuditRepository,
        llm_client: LLMClient
    ):
        self.session = session
        self.relational_repo = relational_repo
        self.vector_repo = vector_repo
        self.audit_repo = audit_repo
        self.llm_client = llm_client

    async def _get_embedding(self, text: str, entity_id: uuid.UUID) -> list[float]:
        """
        Llama al proxy, obtiene el vector y registra la factura en Postgres.
        Todo queda atado a la transacción activa.
        """
        # 1. Petición validada por Pydantic
        request_data = EmbeddingRequest(model="text-embedding", input=text)
        response = await self.llm_client.generate_embedding(request_data)
        
        # 2. Telemetría: Registro del coste usando el repositorio relacional
        await self.audit_repo.create(
            entity_id=entity_id,
            model_used=response.model,
            prompt_tokens=response.usage.prompt_tokens,
            completion_tokens=0,  # En embeddings no hay generación de texto
            total_tokens=response.usage.total_tokens
        )
        
        return response.data[0].embedding

    async def add_memory(
        self, 
        entity_id: uuid.UUID, 
        domain: DomainType, 
        content: str, 
        visibility: Visibility = Visibility.PRIVATE,
        metadata: dict[str, Any] = None
    ) -> uuid.UUID:
        metadata = metadata or {}
        
        try:
            # 1. Inserción Relacional (Flush para generar UUID, SIN commit)
            new_event = await self.relational_repo.create(
                entity_id=entity_id,
                content=content,
                domain=domain,
                visibility=visibility
            )
            
            # 2. IA: Generación de Vector y Registro de Auditoría
            vector = await self._get_embedding(content, entity_id)
            
            # 3. Inserción Vectorial (Qdrant)
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
            
            # 4. Consistencia: Asentamos la transacción y la auditoría
            await self.session.commit()
            logger.info("Memoria híbrida consolidada con éxito: %s", new_event.id)
            return new_event.id
            
        except Exception as e:
            # Cortafuegos: Si algo revienta, abortamos Postgres (Evento + Auditoría)
            await self.session.rollback()
            logger.error("Fallo en la inyección de memoria, transacción abortada: %s", e)
            raise

    async def search_memory(self, entity_id: uuid.UUID, query: str, filters: MemorySearchFilters) -> Sequence[Event]:
        # 1. IA: Vectorizamos la pregunta y facturamos el coste al usuario que busca
        query_vector = await self._get_embedding(query, entity_id)
        
        # 2. Búsqueda Vectorial
        domain = filters.domain or DomainType.SYSTEM
        scored_points = await self.vector_repo.search(
            domain=domain,
            query_vector=query_vector,
            filters=filters
        )
        
        if not scored_points:
            return []
            
        memory_ids = [uuid.UUID(point.id) for point in scored_points]
        
        # 3. Hidratación Relacional
        stmt = select(Event).where(Event.id.in_(memory_ids))
        result = await self.session.execute(stmt)
        hydrated_events = result.scalars().all()
        
        # Falta lógica de ordenación, pero eso no bloquea el Epic
        return hydrated_events