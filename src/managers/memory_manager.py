import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.schemas.memory import DomainType, MemorySearchFilters, Visibility
from src.schemas.llm import EmbeddingRequest, CompletionRequest
from src.repositories.relational import EventRepository, LLMAuditRepository
from src.repositories.vector import VectorRepository
from src.managers.llm_client import LLMClient
from src.db.models import Event

logger = logging.getLogger(__name__)

# Grupo del gateway para generación de texto: LM Studio del host
# ('local-fallback', decisión de David, 23 oct). El resumen de consolidación
# es orquestación de background del worker: el gateway ENRUTA la petición,
# no piensa — pensar es cosa del modelo del inquilino.
CONSOLIDATION_CHAT_MODEL = "local-fallback"

# Ventanas del contrato consolidate_memory: '<entero><h|d|w>' (p. ej. '7d').
_VENTANA_RE = re.compile(r"^(\d+)([hdw])$")
_UNIDADES_VENTANA = {"h": "hours", "d": "days", "w": "weeks"}


def parsear_ventana_temporal(time_window: str) -> timedelta:
    """Traduce una ventana del contrato ('7d', '12h', '2w') a timedelta."""
    match = _VENTANA_RE.match(time_window.strip().lower())
    if not match:
        raise ValueError(
            f"Ventana temporal no válida: '{time_window}'. Formato esperado: "
            f"<entero><h|d|w> (p. ej. '7d', '12h', '2w')."
        )
    cantidad, unidad = match.groups()
    return timedelta(**{_UNIDADES_VENTANA[unidad]: int(cantidad)})


def _visibilidad_mas_restrictiva(eventos: Sequence[Event]) -> Visibility:
    """
    Visibilidad del resumen: la más restrictiva de sus orígenes.

    El resumen mezcla hechos de varios registros: su sensibilidad es la
    máxima de ellos. Un PRIVATE en la mezcla hace el resumen PRIVATE.
    """
    visibilidades = {evento.visibility for evento in eventos}
    if Visibility.PRIVATE in visibilidades:
        return Visibility.PRIVATE
    if Visibility.SHARED in visibilidades:
        return Visibility.SHARED
    return Visibility.PUBLIC


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

    async def _inject_memory(
        self,
        entity_id: uuid.UUID,
        domain: DomainType,
        content: str,
        visibility: Visibility,
        metadata: dict[str, Any]
    ) -> Event:
        """
        Inyección dual SIN commit: evento relacional (flush), embedding
        (con su auditoría) y upsert en Qdrant. El commit pertenece al
        método llamador, dueño de la transacción — así la consolidación
        puede inyectar el resumen Y marcar los orígenes en UNA sola.
        """
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
        return new_event

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
            new_event = await self._inject_memory(
                entity_id=entity_id,
                domain=domain,
                content=content,
                visibility=visibility,
                metadata=metadata
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

    async def _resumir(
        self,
        eventos: Sequence[Event],
        entity_id: uuid.UUID,
        domain: DomainType,
        time_window: str
    ) -> str:
        """
        Resumen denso de los eventos vía gateway (chat completion).

        Regla dura: SOLO hechos presentes en los registros — el resumen ES la
        memoria que queda en el grafo; una alucinación aquí contamina al
        inquilino para siempre. La factura (prompt + completion tokens) queda
        en llm_audit como la de cualquier otra llamada al modelo.
        """
        corpus = "\n".join(f"- {evento.content}" for evento in eventos)
        request = CompletionRequest(
            model=CONSOLIDATION_CHAT_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Eres el módulo de consolidación de memoria de coreAI. "
                        "Resume la lista de registros episódicos en UN párrafo "
                        "denso de hechos. Reglas: solo hechos presentes en los "
                        "registros, sin inventar ni extrapolar; sin listas, "
                        "prosa continua; sin preámbulos ni conclusiones."
                    ),
                },
                {
                    "role": "user",
                    "content": corpus,
                },
            ],
        )
        response = await self.llm_client.generate_completion(request)

        contenido = None
        if response.choices:
            contenido = response.choices[0].message.get("content")
        if not contenido or not str(contenido).strip():
            raise ValueError(
                f"El gateway devolvió un resumen vacío para la consolidación "
                f"(entity {entity_id}, domain {domain.value}, ventana "
                f"{time_window}): modelo sin respuesta o respuesta sin contenido."
            )

        await self.audit_repo.create(
            entity_id=entity_id,
            model_used=response.model,
            prompt_tokens=response.usage.prompt_tokens,
            completion_tokens=response.usage.completion_tokens,
            total_tokens=response.usage.total_tokens
        )
        return str(contenido).strip()

    async def consolidate_memory(
        self,
        entity_id: uuid.UUID,
        domain: DomainType,
        time_window: str
    ) -> uuid.UUID | None:
        """
        Consolida la memoria episódica antigua de un inquilino+dominio (Issue 4.3, #29).

        Agrupa los eventos aún no consolidados anteriores al corte de la
        ventana (domain + time_window), pide al modelo un resumen denso y lo
        reinyecta como contexto denso con inyección dual (Postgres + Qdrant).
        Los orígenes NO se borran: se marcan `consolidated=True` — el log
        relacional es inmutable y el recall decide qué leer. Idempotente por
        construcción: lo ya consolidado nunca se re-procesa; ventana sin
        candidatos es un no-op limpio.

        Returns:
            UUID del evento resumen, o None si la ventana no trajo candidatos.
        """
        cutoff = datetime.now(timezone.utc) - parsear_ventana_temporal(time_window)
        candidatos = await self.relational_repo.list_pending_consolidation(
            entity_id=entity_id,
            domain=domain,
            cutoff=cutoff
        )

        if not candidatos:
            logger.info(
                "Consolidación sin candidatos: nada sin consolidar previo a "
                "%s (entity %s, domain %s, ventana %s) — no-op.",
                cutoff.isoformat(), entity_id, domain.value, time_window,
            )
            return None

        try:
            # 1. Resumen denso vía gateway (LM Studio del host, grupo local-fallback)
            resumen = await self._resumir(candidatos, entity_id, domain, time_window)

            # 2. Reinyección como contexto denso (inyección dual, mismo circuito
            #    que cualquier memoria: handlers únicos writers de la memory layer)
            metadata = {
                "origin": "consolidation",
                "domain": domain.value,
                "time_window": time_window,
                "source_event_ids": [str(evento.id) for evento in candidatos],
            }
            evento_resumen = await self._inject_memory(
                entity_id=entity_id,
                domain=domain,
                content=resumen,
                visibility=_visibilidad_mas_restrictiva(candidatos),
                metadata=metadata
            )

            # 3. Los orígenes quedan marcados: nunca vuelven a una ventana
            await self.relational_repo.mark_consolidated(
                [evento.id for evento in candidatos]
            )

            # 4. Todo vive o muere junto: resumen + vector + marcas, UN commit
            await self.session.commit()
            logger.info(
                "Consolidación completada: %s eventos → resumen %s (entity %s, "
                "domain %s, ventana %s)",
                len(candidatos), evento_resumen.id, entity_id, domain.value,
                time_window,
            )
            return evento_resumen.id

        except Exception as e:
            # Cortafuegos: resumen, evento, vector y marca de orígenes viven o
            # mueren juntos — nada a medias queda en la transacción.
            await self.session.rollback()
            logger.error("Fallo en la consolidación de memoria, transacción abortada: %s", e)
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
