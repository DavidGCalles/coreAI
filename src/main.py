import logging
from fastapi import FastAPI, Request
from starlette.middleware.cors import CORSMiddleware

from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.server.context import ServerRequestContext
from mcp.types import PaginatedRequestParams, CallToolRequestParams

from src.managers.config_manager import config_manager
from src.logging_config import configure_logging

# Importamos exclusivamente nuestras herramientas de ingesta de tareas (Tracer Bullet)
from src.mcp.tools.task_tools import get_task_tools, handle_task_tool

# 1. Inicialización y Configuración
configure_logging(level=config_manager.get_app_config()["log_level"])
logger = logging.getLogger("coreai-mcp")

# 2. Instancia del Servidor MCP (El Cerebro)
mcp_server = Server("coreai-mcp")

# =====================================================================
# 3. REGISTRO CENTRALIZADO DE HERRAMIENTAS MCP
# =====================================================================
@mcp_server.list_tools()
async def handle_list_tools(
    ctx: ServerRequestContext, 
    params: PaginatedRequestParams | None = None
) -> dict:
    """Expone las herramientas disponibles. Adiós vectores, hola intenciones."""
    return {
        "tools": get_task_tools(),
        "nextCursor": None
    }

@mcp_server.call_tool()
async def handle_call_tool(
    ctx: ServerRequestContext, 
    params: CallToolRequestParams
) -> dict:
    """Router central de ejecución para las herramientas."""
    name = params.name
    arguments = params.arguments or {}
    
    # Enrutamos estrictamente a nuestro manejador de tareas
    if name.startswith("coreai_dispatch_"):
        return await handle_task_tool(name, arguments)
        
    # Cualquier intento de llamar a herramientas obsoletas (ej. vectoriales) rebotará aquí
    return {"content": [{"type": "text", "text": f"Herramienta desconocida o no autorizada: {name}"}], "isError": True}

# =====================================================================
# 4. CAPA DE TRANSPORTE HTTP Y SSE
# =====================================================================
sse_transport = SseServerTransport("/messages")

app = FastAPI(
    title="CoreAI",
    description="Memory Backend via Model Context Protocol (Headless & Event-Driven)",
    version="1.0.0"
)

# CORS crítico para clientes externos y extensiones de IDE (Roo Code, Cline, etc.)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/sse")
async def sse_endpoint(request: Request):
    """Establece la conexión unidireccional de eventos (Server-Sent Events)."""
    async with sse_transport.connect_sse(request.scope, request.receive, request._send) as streams:
        read_stream, write_stream = streams
        
        logger.info("Nueva conexión cliente MCP establecida vía SSE.")
        
        # Arrancamos el event loop del servidor para este cliente
        await mcp_server.run(
            read_stream,
            write_stream,
            mcp_server.create_initialization_options()
        )

@app.post("/messages")
async def messages_endpoint(request: Request):
    """
    Recepción de llamadas JSON-RPC (Peticiones de Tools).
    El cliente envía aquí las peticiones y el servidor responde por el canal /sse.
    """
    await sse_transport.handle_post_message(request.scope, request.receive, request._send)

@app.get("/health")
async def health_check():
    """Útil para comprobaciones rápidas de despliegue."""
    return {"status": "operational", "system": "CoreAI MCP Server (Tracer Bullet Active)"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.main:app", host="0.0.0.0", port=8000, reload=True)