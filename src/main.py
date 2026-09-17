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

# 1. Inicialización 
# (logging_config.py usa StreamHandler que escribe en stderr por defecto, 
# VITAL para no corromper el protocolo JSON-RPC que viaja por stdout)
configure_logging(level=config_manager.get_app_config()["log_level"])
logger = logging.getLogger("coreai-mcp")

mcp_server = Server("coreai-mcp")

# =====================================================================
# 2. REGISTRO ESTRICTO (El Router Real y Definitivo)
# =====================================================================
async def handle_list_tools(
    ctx: ServerRequestContext, 
    request: types.ListToolsRequest
) -> types.ListToolsResult:
    """Devuelve las herramientas (get_task_tools ya devuelve objetos Tool puros)."""
    return types.ListToolsResult(
        tools=get_task_tools(),
        nextCursor=None
    )

async def handle_call_tool(
    ctx: ServerRequestContext, 
    request: types.CallToolRequest
) -> types.CallToolResult:
    """Ejecuta y formatea la respuesta. Blindado contra excepciones internas."""
    name = request.params.name
    arguments = request.params.arguments or {}
    
    try:
        if name.startswith("coreai_dispatch_"):
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
        # Aquí está la trampa para osos. 
        # Si SQLAlchemy o Pydantic revientan, capturamos el Traceback completo 
        # y obligamos a Cline a mostrártelo en el chat en lugar de ocultarlo.
        error_trace = traceback.format_exc()
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=f"Fallo interno crítico en coreAI:\n\n{error_trace}")],
            isError=True
        )

# El orden estricto es: (nombre_del_metodo, clase_del_request, funcion_manejadora)
mcp_server.add_request_handler("tools/list", types.ListToolsRequest, handle_list_tools)
mcp_server.add_request_handler("tools/call", types.CallToolRequest, handle_call_tool)

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