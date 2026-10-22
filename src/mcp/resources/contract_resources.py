"""
Exposición de Contratos como MCP Resources — ADR-006-002 (#42).

Descubrimiento antes que aplicación: el flujo del cliente empieza por
descubrir el contrato, y la frontera de validación presupone que este
catálogo ya existe.

Superficies — servidas TODAS desde el mismo objeto de registro del
ADR-006-001: lo que lee el cliente es exactamente lo que validará el
dispatch (fuente única de verdad, cero duplicación).

- resources/list: raíz navegable (índice) + un resource por tipo contratado.
- resources/read sobre la raíz `coreai://contracts/tasks`: índice del catálogo
  (tipos, descripciones, URIs). El modelo mental REST no desaparece del
  colectivo: un agente que reciba la URI del catálogo en un error del
  dispatch puede resolverla directamente, sin pasar antes por resources/list
  (decisión de David, 21 oct).
- resources/read sobre `coreai://contracts/tasks/{task_type}`: JSON Schema
  del payload del tipo (los Field descriptions van embebidos en el schema).
"""

import json
import logging
import re

import mcp.types as types
from mcp.shared.exceptions import MCPError
from mcp.types import Error

from src.contracts import CONTRACT_URI_TEMPLATE, task_contract_registry
from src.schemas.tasks import TaskType

logger = logging.getLogger(__name__)

# Códigos de error del protocolo (especificación MCP sobre JSON-RPC 2.0):
# -32002 Resource Not Found, -32602 Invalid params.
RESOURCE_NOT_FOUND = -32002
INVALID_PARAMS = -32602

CONTRACTS_ROOT_URI = CONTRACT_URI_TEMPLATE.rsplit("/", 1)[0]  # coreai://contracts/tasks

# Template por tipo: segmento final sin '/', colgado de la raíz.
_CONTRACT_URI_RE = re.compile("^" + re.escape(CONTRACTS_ROOT_URI) + r"/([^/]+)$")

CATALOG_MIME_TYPE = "application/json"           # la raíz es un índice, no un schema
CONTRACT_MIME_TYPE = "application/schema+json"   # el schema por tipo ES JSON Schema

ROOT_RESOURCE_NAME = "coreai-contracts-catalog"
ROOT_RESOURCE_DESCRIPTION = (
    "Índice del catálogo de contratos de tarea del Córtex Asíncrono: tipos "
    "contratados, sus descripciones y sus URIs (tipos despachables vía "
    "coreai_dispatch_task). Leer aquí es el punto de entrada del descubrimiento: "
    "cada URI de tipo devuelve el JSON Schema que validará el dispatch."
)

_SCHEMA_DISCLAIMER = (
    "Lo que lees aquí es EXACTAMENTE lo que validará coreai_dispatch_task de "
    "forma síncrona (misma fuente de verdad: el registro central)."
)


def _resource_not_found(message: str) -> MCPError:
    """Error de protocolo -32002: resource no resoluble por este servidor."""
    return MCPError(error=Error(code=RESOURCE_NOT_FOUND, message=message))


def get_contract_resources() -> list[types.Resource]:
    """Catálogo publicado en resources/list: raíz + un resource por tipo contratado."""
    resources = [
        types.Resource(
            uri=CONTRACTS_ROOT_URI,
            name=ROOT_RESOURCE_NAME,
            description=ROOT_RESOURCE_DESCRIPTION,
            mimeType=CATALOG_MIME_TYPE,
        )
    ]
    for entry in task_contract_registry.catalog():
        resources.append(
            types.Resource(
                uri=entry["uri"],
                name=f"contract:{entry['task_type']}",
                description=f"{entry['description']} {_SCHEMA_DISCLAIMER}",
                mimeType=CONTRACT_MIME_TYPE,
            )
        )
    return resources


async def read_contract_resource(uri: str | None) -> types.ReadResourceResult:
    """Resuelve resources/read: raíz → índice del catálogo; template → JSON Schema.

    Raises:
        McpError: -32602 si falta la URI; -32002 si la URI está fuera del
        espacio de contratos, el tipo no existe o no tiene contrato registrado.
    """
    uri = (uri or "").strip()
    if not uri:
        raise MCPError(
            error=Error(
                code=INVALID_PARAMS,
                message="resources/read requiere el parámetro 'uri' (string).",
            )
        )

    # Raíz: índice del catálogo. El agente con modelo mental REST que reciba
    # la URI del catálogo en un error del dispatch la resuelve tal cual.
    if uri == CONTRACTS_ROOT_URI:
        index = {
            "catalog": CONTRACTS_ROOT_URI,
            "contract_uri_template": CONTRACT_URI_TEMPLATE,
            "contracts": task_contract_registry.catalog(),
        }
        logger.debug("resources/read del catálogo de contratos (raíz)")
        return types.ReadResourceResult(
            contents=[
                types.TextResourceContents(
                    uri=uri,
                    mimeType=CATALOG_MIME_TYPE,
                    text=json.dumps(index, ensure_ascii=False, indent=2),
                )
            ]
        )

    # Template por tipo: coreai://contracts/tasks/{task_type}
    match = _CONTRACT_URI_RE.match(uri)
    if match is None:
        raise _resource_not_found(
            f"La URI '{uri}' no pertenece al espacio de contratos de coreAI. "
            f"Raíz del catálogo: '{CONTRACTS_ROOT_URI}'. "
            f"Template por tipo: '{CONTRACT_URI_TEMPLATE}'."
        )

    raw_task_type = match.group(1)
    try:
        task_type = TaskType(raw_task_type)
    except ValueError:
        contracted = ", ".join(task_contract_registry.list()) or "ninguno"
        raise _resource_not_found(
            f"'{raw_task_type}' no es un TaskType del sistema. "
            f"Tipos con contrato: {contracted}."
        )

    if not task_contract_registry.has_contract(task_type):
        contracted = ", ".join(task_contract_registry.list()) or "ninguno"
        raise _resource_not_found(
            f"El tipo '{raw_task_type}' existe pero NO tiene contrato registrado "
            f"(sin contrato no hay despacho, ADR-006-001). "
            f"Tipos con contrato: {contracted}."
        )

    schema_text = json.dumps(
        task_contract_registry.schema(task_type),
        ensure_ascii=False,
        indent=2,
    )
    logger.debug("resources/read del contrato: %s", raw_task_type)
    return types.ReadResourceResult(
        contents=[
            types.TextResourceContents(
                uri=uri,
                mimeType=CONTRACT_MIME_TYPE,
                text=schema_text,
            )
        ]
    )
