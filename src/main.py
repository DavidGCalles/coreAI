import logging
from fastapi import FastAPI, Request
from starlette.middleware.cors import CORSMiddleware
from mcp.server import Server
from mcp.server.sse import SseServerTransport

from src.mcp.tools.vector_tools import register_vector_tools, register_vector_resources
from src.managers.config_manager import config_manager
from src.logging_config import configure_logging

# 1. Inicialización y Configuración
configure_logging(level=config_manager.get_app_config()["log_level"])
logger = logging.getLogger("coreai-mcp")

# 2. Instancia del Servidor MCP (El Cerebro)
mcp_server = Server("coreai-mcp")

# Inyectamos las herramientas vectoriales que hemos purgado
register_vector_tools(mcp_server)
register_vector_resources(mcp_server)

# 3. Transporte SSE (Las Arterias)
# El endpoint /messages será donde el cliente envíe sus peticiones POST
sse_transport = SseServerTransport("/messages")

# 4. Capa HTTP (La Piel)
app = FastAPI(
    title="CoreAI",
    description="Memory Backend via Model Context Protocol",
    version="1.0.0"
)

# CORS crítico para clientes externos y extensiones de IDE
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/sse")
async def sse_endpoint(request: Request):
    """
    Establece la conexión unidireccional de eventos (Server-Sent Events).
    El cliente se queda escuchando aquí.
    """
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
    Recepción de llamadas JSON-RPC.
    El cliente envía aquí las peticiones de Tools y el servidor responde por el canal /sse.
    """
    await sse_transport.handle_post_message(request.scope, request.receive, request._send)


# --- Útil para comprobaciones rápidas de despliegue ---
@app.get("/health")
async def health_check():
    return {"status": "operational", "system": "CoreAI MCP Server"}

if __name__ == "__main__":
    import uvicorn
    # En producción o docker, esto se lanza vía CLI: uvicorn src.main:app --host 0.0.0.0 --port 8000
    uvicorn.run("src.main:app", host="0.0.0.0", port=8000, reload=True)