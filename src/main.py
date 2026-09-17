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

async def handle_list_tools(
    ctx: ServerRequestContext, 
    request: PassthroughRequest
) -> types.ListToolsResult:
    """Devuelve las herramientas delegando en el registro dinámico."""
    return types.ListToolsResult(
        tools=get_task_tools(),
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
        if name and name.startswith("coreai_dispatch_"):
            raw_response = await handle_task_tool(name, arguments)
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
        logger.error(f"Fallo interno crítico en coreAI:\n\n{error_trace}")
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=f"Fallo interno crítico en coreAI:\n\n{error_trace}")],
            isError=True
        )

# Registramos las rutas inyectando nuestro modelo permisivo
mcp_server.add_request_handler("tools/list", PassthroughRequest, handle_list_tools)
mcp_server.add_request_handler("tools/call", PassthroughRequest, handle_call_tool)

# =====================================================================
# 3. EL CEREBRO STDIO (Sin red, sin FastAPI)
# =====================================================================
async def main():
    logger.info("Iniciando CoreAI MCP Server vía STDIO...")
    
    # stdio_server() secuestra stdin/stdout para hablar directamente con el IDE
    async with stdio_server() as (read_stream, write_stream):
        await mcp_server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="coreai-mcp",
                server_version="1.0.0",
                capabilities=types.ServerCapabilities(
                    tools=types.ToolsCapability(listChanged=False)
                )
            )
        )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Servidor MCP detenido manualmente.")