import logging
from typing import Callable
from mcp.server import Server
from mcp.types import Tool, Resource
from mcp.server.context import ServerRequestContext
from mcp.types import PaginatedRequestParams, CallToolRequestParams, ReadResourceRequestParams, TextResourceContents
from src.managers.memory_manager import VectorMemoryManager
from src.schemas.memory import CoreMemoryNode, MemorySearchFilters, DomainType
from pydantic import AnyUrl, BaseModel


logger = logging.getLogger(__name__)
vector_manager = VectorMemoryManager()

def register_vector_tools(server: Server):
    """Registra las capacidades vectoriales en el servidor MCP."""
    
    async def list_tools_impl(
        ctx: ServerRequestContext, 
        params: PaginatedRequestParams | None = None
    ):
        return {
            "tools": [
                Tool(
                    name="coreai_search_context",
                    description="Busca en la memoria vectorial de CoreAI usando lenguaje natural.",
                    inputSchema=MemorySearchFilters.model_json_schema()
                ),
                Tool(
                    name="coreai_store_context",
                    description="Persiste nuevo conocimiento en la memoria vectorial.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "tenant_id": {"type": "string"},
                            "domain": {"type": "string", "enum": [d.value for d in DomainType]},
                            "content": {"type": "string"},
                            "tags": {"type": "array", "items": {"type": "string"}}
                        },
                        "required": ["tenant_id", "domain", "content"]
                    }
                )
            ],
            "nextCursor": None
        }

    async def call_tool_impl(
        ctx: ServerRequestContext, 
        params: CallToolRequestParams
    ):
        name = params.name
        arguments = params.arguments or {}

        if not arguments:
            raise ValueError("Argumentos requeridos.")

        try:
            if name == "coreai_search_context":
                filters = MemorySearchFilters.model_validate(arguments)
                results = await vector_manager.search_memory(
                    query=filters.query, 
                    filters=filters
                )
                
                if not results:
                    return {
                        "content": [{"type": "text", "text": "Sin resultados relevantes."}],
                        "isError": False
                    }
                
                output = "\n\n".join([f"[{r.domain}] {r.content}" for r in results])
                return {"content": [{"type": "text", "text": output}], "isError": False}

            elif name == "coreai_store_context":
                node = CoreMemoryNode(
                    tenant_id=arguments["tenant_id"],
                    domain=DomainType(arguments["domain"]),
                    content=arguments["content"]
                )
                if "tags" in arguments:
                    node.metadata.tags = arguments["tags"]
                
                node_id = await vector_manager.add_memory(node)
                return {"content": [{"type": "text", "text": f"Guardado con ID: {node_id}"}], "isError": False}

            raise ValueError(f"Herramienta {name} no encontrada.")

        except Exception as e:
            logger.error(f"Error en tool {name}: {e}")
            return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "isError": True}

    server.on_list_tools = list_tools_impl
    server.on_call_tool = call_tool_impl


def register_vector_resources(server: Server):
    """Registra la introspección de Qdrant como recursos MCP."""

    async def list_resources_impl(
        ctx: ServerRequestContext, 
        params: PaginatedRequestParams | None = None
    ):
        """Expone las colecciones de Qdrant como recursos direccionables."""
        # Obtenemos las colecciones reales configuradas en el manager
        collections = vector_manager._collections  # [document, conversation, system]
        
        return {
            "resources": [
                Resource(
                    uri=AnyUrl(f"qdrant://{coll}/metadata"),
                    name=f"Metadatos de la colección {coll}",
                    description=f"Estado actual, conteo de vectores y configuración de {coll}",
                    mimeType="application/json"
                ) for coll in collections
            ],
            "nextCursor": None
        }

    async def read_resource_impl(ctx: ServerRequestContext, params: ReadResourceRequestParams):
        """Lee el estado actual de una colección específica."""
        uri_str = str(params.uri)
        if not uri_str.startswith("qdrant://"):
            raise ValueError("URI no soportada")

        # Extraemos el nombre de la colección de la URI
        collection_name = uri_str.split("://")[1].split("/")[0]
        
        # Necesitarás implementar un método get_collection_info en tu VectorMemoryManager
        stats = await vector_manager.get_collection_info(collection_name) 
        
        import json
        return {
            "contents": [TextResourceContents(
                uri=AnyUrl(uri_str),
                mimeType="application/json",
                text=json.dumps(stats, indent=2)
            )],
            "isError": False
        }

    server.on_list_resources = list_resources_impl
    server.on_read_resource = read_resource_impl