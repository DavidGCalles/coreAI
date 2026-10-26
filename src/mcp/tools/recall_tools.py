"""
Tools MCP de recall síncrono read-only (ADR-005-002).

Apertura síncrona ACOTADA del sustrato (enmienda ADR-005, 26 oct): el recall
es request-response y no crea tasks — el async lane queda SOLO para la
ingesta. Capa fina estricta: valida la forma en el borde (antes de abrir
cualquier sesión), delega la política en HybridMemoryManager y formatea
la respuesta. Cero escrituras: sin dispatch, sin factura en llm_audit, y
la consulta (texto o imagen) nunca se persiste.

Higiene de superficies: descripciones escuetas y funcionales, sin
numeración interna en lo que ven los clientes.
"""

import logging

from mcp.types import Tool

from src.db.database import AsyncSessionLocal
from src.db.vector_db import get_qdrant_client
from src.repositories.relational import EventRepository, LLMAuditRepository
from src.repositories.vector import VectorRepository
from src.managers.memory_manager import HybridMemoryManager, MissingCollectionError
from src.managers.llm_client import LLMClient
from src.schemas.recall import RecallImageRequest, RecallResponse, RecallTextRequest

logger = logging.getLogger(__name__)

SEARCH_CONTEXT_DESCRIPTION = (
    "Búsqueda semántica read-only sobre la memoria de texto: "
    "recorre todas las colecciones de texto, ordena por similitud y devuelve "
    "los top_k resultados con su score y su colección de origen. Sin "
    "resultados sobre el umbral = no-match honesto (lista vacía). Cero "
    "mutación: no crea tareas ni escribe memoria; la consistencia con las "
    "ingestas es eventual — consulta las tareas con coreai_get_task."
)

SEARCH_IMAGE_DESCRIPTION = (
    "Búsqueda por similitud visual read-only (identify): envía una imagen de "
    "consulta (data URI raster) y devuelve los top_k puntos más parecidos de "
    "la memoria de imágenes con su score, o un no-match honesto si nada "
    "supera el umbral. La imagen de consulta NO se persiste (embed efímero) "
    "y no se crea ninguna tarea."
)


def get_recall_tools() -> list[Tool]:
    """Devuelve las herramientas de recall para el registro central."""
    return [
        Tool(
            name="coreai_search_context",
            description=SEARCH_CONTEXT_DESCRIPTION,
            inputSchema=RecallTextRequest.model_json_schema(),
        ),
        Tool(
            name="coreai_search_image",
            description=SEARCH_IMAGE_DESCRIPTION,
            inputSchema=RecallImageRequest.model_json_schema(),
        ),
    ]


async def handle_recall_tool(name: str, arguments: dict) -> dict:
    """Manejador de las tools de recall: validar → delegar en el manager → formatear."""

    if name not in ("coreai_search_context", "coreai_search_image"):
        raise ValueError(f"Herramienta {name} no es gestionada por recall_tools.")

    request_model = RecallTextRequest if name == "coreai_search_context" else RecallImageRequest

    # 1. Validación de forma en el borde, ANTES de abrir sesión: un argumento
    #    malformado jamás abre transacción (DoD: negativos sin transacción).
    try:
        request = request_model.model_validate(arguments)
    except Exception as e:
        logger.warning("Argumentos malformados en %s: %s", name, e)
        return {
            "content": [{"type": "text", "text": f"Error de validación: {e}"}],
            "isError": True,
        }

    try:
        # 2. Capa fina: la sesión efímera solo satisface la constructora del
        #    manager híbrido — el recall no ejecuta NI UNA consulta SQL: sin
        #    SELECT que abra transacción, sin commit ni flush, la garantía
        #    read-only es por construcción.
        async with AsyncSessionLocal() as session:
            vector_repo = VectorRepository(client=await get_qdrant_client())
            manager = HybridMemoryManager(
                session=session,
                relational_repo=EventRepository(session),
                vector_repo=vector_repo,
                audit_repo=LLMAuditRepository(session),
                llm_client=LLMClient(),
            )

            if name == "coreai_search_context":
                hits = await manager.recall_text(
                    query=request.query,
                    project=request.project,
                    top_k=request.top_k,
                    score_threshold=request.score_threshold,
                )
            else:
                hits = await manager.recall_image(
                    image_data_uri=request.image,
                    project=request.project,
                    top_k=request.top_k,
                    score_threshold=request.score_threshold,
                )

            response = RecallResponse(hits=hits)
            return {
                "content": [{"type": "text", "text": response.model_dump_json(indent=2)}],
                "isError": False,
            }

    except MissingCollectionError as e:
        # Error EXPLÍCITO que suena (decisión de David, 26 oct): la tool se
        # niega antes que devolver un falso vacío — un fallo de inicialización
        # no se esconde detrás de un no-match honesto.
        logger.error("Recall bloqueado por colección inexistente: %s", e)
        return {"content": [{"type": "text", "text": str(e)}], "isError": True}
    except Exception as e:
        logger.error("Fallo estructural en %s: %s", name, e)
        return {"content": [{"type": "text", "text": f"Fallo estructural: {e}"}], "isError": True}
