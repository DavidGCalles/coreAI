import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.schemas.memory import DomainType, MemorySearchFilters, Visibility
from src.schemas.llm import (
    CompletionRequest,
    EmbeddingRequest,
    IMAGE_EMBEDDING_MODEL,
    TEXT_EMBEDDING_MODEL,
)
from src.schemas.recall import MemoryHit
from src.repositories.relational import EventRepository, LLMAuditRepository
from src.repositories.vector import IMAGE_COLLECTION, VectorRepository
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


class MissingCollectionError(RuntimeError):
    """
    Colección de Qdrant ausente para un recall (ADR-005-002, #37).

    El recall se niega antes que mentir: una colección que no existe no es
    un no-match honesto — es un fallo de inicialización del sustrato, y el
    cliente tiene que oírlo como error explícito (decisión de David, 26
    oct: los errores suenan, no se esconden detrás de listas vacías).
    """


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
        # 1. Petición validada por Pydantic (id de grupo: única fuente de
        #    verdad en schemas/llm.py — lección de la PR #63)
        request_data = EmbeddingRequest(model=TEXT_EMBEDDING_MODEL, input=text)
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
        metadata: dict[str, Any],
        project: str | None = None
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

        # 3. Inserción Vectorial (Qdrant). 'project' viaja como clave plana
        #    del payload (FR-3, #51), simétrica con los puntos de imagen: el
        #    filtro del recall la comparte en texto e imagen.
        payload = {
            "tenant_id": str(entity_id),
            "content": content,
            "metadata": metadata,
            "project": project
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
        metadata: dict[str, Any] = None,
        project: str | None = None
    ) -> uuid.UUID:
        metadata = metadata or {}
        
        try:
            new_event = await self._inject_memory(
                entity_id=entity_id,
                domain=domain,
                content=content,
                visibility=visibility,
                metadata=metadata,
                project=project
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

        Limitaciones conocidas de la v1 (BUG-006 #69): la agrupación es
        puramente temporal (inquilino + dominio + ventana) — no hay criterio
        semántico. Una ventana heterogénea fusiona recuerdos sin relación
        en un mismo resumen, y ese resumen queda embebido en Qdrant y se
        sirve en cada recall futuro: contaminación semántica permanente del
        inquilino. La evolución (consolidación por temática solicitada,
        selección semántica con límite de cantidad en tokens) vive en el
        BUG-006 #69.

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

    # =================================================================
    # RECALL SÍNCRONO READ-ONLY (ADR-005-002, #37)
    # =================================================================
    # Apertura síncrona ACOTADA del sustrato (enmienda ADR-005, 26 oct):
    # el recall es request-response y vive AQUÍ, en el manager — la tool
    # MCP es capa fina. Cero escrituras: ni dispatch, ni factura en
    # llm_audit, ni commit; la consulta (texto o imagen) es cómputo
    # efímero que nunca se persiste.

    # La resolución de identidad multi-tenant queda DIFERIDA a su propia
    # pieza de diseño: el recall v1 no filtra por tenant. Los puntos
    # conservan tenant_id en su payload (la ingesta lo escribe), así
    # que restaurar el aislamiento es un cambio local en el filtro.

    async def _embed_query_texto(self, query: str) -> list[float]:
        """
        Embed EFÍMERO de la consulta de texto.

        Mismo gateway que la ingesta, telemetría distinta: la consulta es
        cómputo read-only, no un dato del inquilino — no toca llm_audit
        (el _get_embedding de la ingesta sí factura, atado a su transacción).
        """
        request = EmbeddingRequest(model=TEXT_EMBEDDING_MODEL, input=query)
        response = await self.llm_client.generate_embedding(request)
        if not response.data:
            raise ValueError(
                "El gateway devolvió un embedding vacío para la consulta — "
                "sin vector no hay recall."
            )
        return response.data[0].embedding

    async def _embed_query_imagen(self, image_data_uri: str) -> list[float]:
        """
        Embed EFÍMERO de la imagen de consulta (identify, ADR-007-002).

        Grupo 'image-embedding' (jina-clip-v2) con modality='image'. La
        imagen de consulta es cómputo, no memoria: nunca se persiste, no
        se factura — a diferencia de la ingesta de imagen, que va por el
        async lane con auditoría completa.
        """
        request = EmbeddingRequest(
            model=IMAGE_EMBEDDING_MODEL,
            input=image_data_uri,
            modality="image",
        )
        response = await self.llm_client.generate_embedding(request)
        if not response.data:
            raise ValueError(
                "El gateway devolvió un embedding vacío para la imagen de "
                "consulta — sin vector no hay recall visual."
            )
        return response.data[0].embedding

    @staticmethod
    def _hit_desde_punto(point: Any, domain: str) -> MemoryHit:
        """
        Traduce un ScoredPoint crudo de Qdrant a un hit plano del contrato.

        Claves planas del payload, sin jerarquías: lo que ve el cliente es
        lo que hay en el punto (higiene de superficies, decisión 26 oct).
        """
        payload = point.payload or {}
        return MemoryHit(
            point_id=str(point.id),
            domain=domain,
            score=point.score,
            content=payload.get("content"),
            session_id=payload.get("session_id"),
            project=payload.get("project"),
        )

    async def recall_text(
        self,
        query: str,
        project: str | None = None,
        top_k: int = 5,
        score_threshold: float = 0.7
    ) -> list[MemoryHit]:
        """
        Recall de texto: sweep por el catálogo DomainType + merge global.

        Recorre TODAS las colecciones de texto con los mismos filtros,
        fusiona los resultados en un solo ranking por score y recorta a
        top_k DESPUÉS del merge (decisión 26 oct): el ranking pertenece a la
        consulta, no a la colección. Cada hit lleva su colección de origen.

        Colección inexistente = MissingCollectionError ANTES de embeber:
        un fallo de inicialización no se disfraza de no-match honesto.
        """
        existentes = await self.vector_repo.collection_names()
        faltantes = [d.value for d in DomainType if d.value not in existentes]
        if faltantes:
            raise MissingCollectionError(
                f"Recall bloqueado: colecciones inexistentes en el vector "
                f"store ({', '.join(faltantes)}). El sustrato no está "
                f"inicializado — esto es un fallo de arranque, no un no-match "
                f"de la búsqueda."
            )

        query_vector = await self._embed_query_texto(query)

        hits: list[MemoryHit] = []
        for domain in DomainType:
            filters = MemorySearchFilters(
                project=project,
                limit=top_k,
                score_threshold=score_threshold
            )
            puntos = await self.vector_repo.search(
                domain=domain,
                query_vector=query_vector,
                filters=filters
            )
            hits.extend(
                self._hit_desde_punto(punto, domain.value) for punto in puntos
            )

        # Merge global por score, top_k tras el merge
        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits[:top_k]

    async def recall_image(
        self,
        image_data_uri: str,
        project: str | None = None,
        top_k: int = 5,
        score_threshold: float = 0.5
    ) -> list[MemoryHit]:
        """
        Recall visual (identify): similitud contra la colección de imágenes.

        Simétrico con el recall de texto (misma firma de filtros, mismo
        hit plano) sobre el espacio de visión (jina-clip-v2, 1024 dims).
        La imagen de consulta NO se persiste: embed efímero. Sin matches
        sobre el umbral = no-match honesto (lista vacía).
        """
        existentes = await self.vector_repo.collection_names()
        if IMAGE_COLLECTION not in existentes:
            raise MissingCollectionError(
                f"Recall visual bloqueado: la colección '{IMAGE_COLLECTION}' "
                f"no existe en el vector store. El sustrato de visión no "
                f"está inicializado — fallo de arranque, no un no-match de la "
                f"búsqueda."
            )

        query_vector = await self._embed_query_imagen(image_data_uri)

        filters = MemorySearchFilters(
            project=project,
            limit=top_k,
            score_threshold=score_threshold
        )
        puntos = await self.vector_repo.search(
            domain=IMAGE_COLLECTION,
            query_vector=query_vector,
            filters=filters
        )
        return [self._hit_desde_punto(punto, IMAGE_COLLECTION) for punto in puntos]
