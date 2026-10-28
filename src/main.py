import argparse
import asyncio
import logging
import sys
import traceback

from mcp.server import Server, InitializationOptions
import mcp.types as types
from mcp.server.stdio import stdio_server
from mcp.server.context import ServerRequestContext

from src.managers.config_manager import config_manager
from src.logging_config import configure_logging
from src.mcp.tools.task_tools import get_task_tools, handle_task_tool
from src.mcp.tools.recall_tools import get_recall_tools, handle_recall_tool
from src.mcp.resources.contract_resources import get_contract_resources, read_contract_resource

from pydantic import BaseModel, ConfigDict
from typing import Any

# 1. Inicialización 
# (logging_config.py usa StreamHandler que escribe en stderr por defecto, 
# VITAL para no corromper el protocolo JSON-RPC que viaja por stdout)
configure_logging(level=config_manager.get_app_config()["log_level"])
logger = logging.getLogger("coreai-mcp")

mcp_server = Server("coreai-mcp")

# =====================================================================
# 2. REGISTRO ESTRICTO (El Router Real y Definitivo)
# =====================================================================

# Evita el error -32602 del SDK absorbiendo el JSON sin validaciones restrictivas.
class PassthroughRequest(BaseModel):
    model_config = ConfigDict(extra='allow')
    params: dict[str, Any] | None = None
    name: str | None = None
    arguments: dict[str, Any] | None = None

# El router real delega en el registro dinámico: se enruta por nombre
# registrado, no por prefijo hardcodeado — así el router queda estable
# cuando la lista de herramientas crece (BUG-003, #53: coreai_get_task
# no comparte prefijo con coreai_dispatch_task). Dos familias, dos
# manejadores: tasks (async lane) y recall (síncrono read-only, #37).
_TASK_TOOL_NAMES = frozenset(tool.name for tool in get_task_tools())
_RECALL_TOOL_NAMES = frozenset(tool.name for tool in get_recall_tools())
_REGISTERED_TOOL_NAMES = _TASK_TOOL_NAMES | _RECALL_TOOL_NAMES

async def handle_list_tools(
    ctx: ServerRequestContext, 
    request: PassthroughRequest
) -> types.ListToolsResult:
    """Devuelve las herramientas delegando en el registro dinámico."""
    return types.ListToolsResult(
        tools=get_task_tools() + get_recall_tools(),
        nextCursor=None
    )

async def handle_call_tool(
    ctx: ServerRequestContext, 
    request: PassthroughRequest
) -> types.CallToolResult:
    """Ejecuta y formatea la respuesta. Blindado contra excepciones internas."""
    
    # Extraemos los datos sea cual sea el nivel de anidación que use el SDK internamente
    name = request.name or (request.params.get("name") if request.params else None)
    arguments = request.arguments or (request.params.get("arguments", {}) if request.params else {})
    
    try:
        if name and name in _REGISTERED_TOOL_NAMES:
            # Dos familias de tools, dos manejadores: mismo formato de
            # respuesta, distinta política (dispatch async vs. recall
            # read-only síncrono).
            handler = handle_task_tool if name in _TASK_TOOL_NAMES else handle_recall_tool
            raw_response = await handler(name, arguments)
            text_output = raw_response["content"][0]["text"]
            is_error = raw_response.get("isError", False)
            
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=text_output)],
                isError=is_error
            )
                    
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=f"Herramienta desconocida: {name}")],
            isError=True
        )
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        logger.error(f"Fallo interno crítico en coreAI: {error_trace}")
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=f"Fallo interno crítico en coreAI: \
                                       {error_trace}")],
            isError=True
        )

# =====================================================================
# 2b. RESOURCES (ADR-006-002, #42): Exposición de Contratos
# =====================================================================
# Descubrimiento antes que aplicación. El mismo patrón PassthroughRequest
# (Caballo de Troya) que tools: si el SDK aplicara a los handlers de resources
# la validación estricta que causó el -32602 del post-mortem ADR-004, el
# modelo permisivo absorbe el JSON tal cual y la resolución es nuestra.
# Todo se sirve desde el registro central (ADR-006-001): lo que lee el
# cliente es lo que validará el dispatch. La raíz coreai://contracts/tasks
# es legible como índice — el modelo mental REST del colectivo queda
# previsto (decisión de David, 21 oct).

async def handle_list_resources(
    ctx: ServerRequestContext, 
    request: PassthroughRequest
) -> types.ListResourcesResult:
    """Publica el catálogo de contratos: raíz navegable + un resource por tipo."""
    return types.ListResourcesResult(
        resources=get_contract_resources(),
        nextCursor=None
    )

async def handle_read_resource(
    ctx: ServerRequestContext, 
    request: PassthroughRequest
) -> types.ReadResourceResult:
    """Resuelve la lectura: raíz → índice del catálogo; template → JSON Schema."""
    # Extraemos la URI sea cual sea el nivel de anidación que use el SDK internamente
    uri = getattr(request, "uri", None) or (request.params.get("uri") if request.params else None)
    return await read_contract_resource(uri)

# Registramos las rutas inyectando nuestro modelo permisivo
mcp_server.add_request_handler("tools/list", PassthroughRequest, handle_list_tools)
mcp_server.add_request_handler("tools/call", PassthroughRequest, handle_call_tool)
mcp_server.add_request_handler("resources/list", PassthroughRequest, handle_list_resources)
mcp_server.add_request_handler("resources/read", PassthroughRequest, handle_read_resource)

# =====================================================================
# 3. SERVING (ADR-008-002): el mismo server, dos transportes.
#    STDIO queda intacto como vía interina documentada (patrón IDE/host:
#    docker exec spawnea este módulo igual que siempre). Streamable HTTP
#    se sirve sobre la MISMA instancia — el propio Server del SDK monta
#    el Starlette — sin duplicar registro ni handlers.
# =====================================================================

def create_initialization_options() -> InitializationOptions:
    """Opciones de handshake compartidas por todos los transportes."""
    return InitializationOptions(
        server_name="coreai-mcp",
        server_version="1.0.0",
        capabilities=types.ServerCapabilities(
            tools=types.ToolsCapability(listChanged=False),
            resources=types.ResourcesCapability(listChanged=False)
        )
    )


async def run_stdio() -> None:
    """El cerebro STDIO de siempre: stdin/stdout secuestrados para el IDE."""
    logger.info("Iniciando CoreAI MCP Server vía STDIO...")
    
    # stdio_server() secuestra stdin/stdout para hablar directamente con el IDE
    async with stdio_server() as (read_stream, write_stream):
        await mcp_server.run(
            read_stream,
            write_stream,
            create_initialization_options()
        )


def _http_app(host: str):
    """Starlette de streamable HTTP construido por el propio Server del SDK.

    Sesiones stateful (`Mcp-Session-Id`) por defecto, path `/mcp`. El host
    viaja al app para que la protección DNS-rebinding solo se active en
    localhost: en la red interna del compose el perímetro ES la propia
    red — sin auth, decisión explícita del ADR.
    """
    return mcp_server.streamable_http_app(host=host)


def run_http(host: str, port: int) -> None:
    """Serving únicamente por streamable HTTP (el modo de despliegue)."""
    import uvicorn

    logger.info(
        "Iniciando CoreAI MCP Server vía streamable HTTP en http://%s:%s/mcp ...",
        host, port
    )
    uvicorn.run(_http_app(host), host=host, port=port, log_level="warning")


def run_dual(host: str, port: int) -> None:
    """Una instancia de Server, dos transportes, un proceso.

    HTTP vive en su hilo (uvicorn y el session manager con su propio event
    loop); el hilo principal sirve STDIO. Cada sesión vive entera en un
    solo loop, pero el pool de asyncpg es compartido: mezclar USO REAL de
    base de datos por ambos transportes a la vez puede cruzar loops. El
    despliegue normal no lo hace — cada transporte en su proceso (el
    service del compose en modo http; cada docker exec de IDE, su stdio).
    """
    import threading

    import uvicorn

    config = uvicorn.Config(_http_app(host), host=host, port=port, log_level="warning")
    http_server = uvicorn.Server(config)
    threading.Thread(target=http_server.run, daemon=True, name="coreai-http").start()
    logger.info(
        "Modo dual: streamable HTTP en http://%s:%s/mcp (hilo) + STDIO en el proceso principal",
        host, port
    )
    asyncio.run(run_stdio())


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Frontera MCP de coreAI: mismo server, STDIO y streamable HTTP."
    )
    parser.add_argument(
        "--serving",
        choices=["stdio", "http", "dual"],
        default="stdio",
        help="Vía de serving: stdio (default, patrón Cline), http (streamable HTTP), dual (ambos, un proceso).",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Binding de http/dual (default 0.0.0.0: la red interna del compose).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Puerto de http/dual (default 8000: una sola fuente de verdad de puertos).",
    )
    args = parser.parse_args()

    if args.serving == "http":
        run_http(args.host, args.port)
    elif args.serving == "dual":
        run_dual(args.host, args.port)
    else:
        asyncio.run(run_stdio())


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Servidor MCP detenido manualmente.")
