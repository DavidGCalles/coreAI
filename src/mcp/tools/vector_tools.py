import logging
from mcp.server import Server
from mcp.types import Tool, TextContent
from src.managers.memory_manager import VectorMemoryManager
from src.schemas.memory import CoreMemoryNode, MemorySearchFilters, DomainType

logger = logging.getLogger(__name__)
vector_manager = VectorMemoryManager()

def register_vector_tools(server: Server):
    """Registra las capacidades vectoriales en el servidor MCP."""
    
    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
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
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict | None) -> list[TextContent]:
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
                    return [TextContent(type="text", text="Sin resultados relevantes.")]
                
                output = "\n\n".join([f"[{r.domain}] {r.content}" for r in results])
                return [TextContent(type="text", text=output)]

            elif name == "coreai_store_context":
                node = CoreMemoryNode(
                    tenant_id=arguments["tenant_id"],
                    domain=DomainType(arguments["domain"]),
                    content=arguments["content"]
                )
                if "tags" in arguments:
                    node.metadata.tags = arguments["tags"]
                
                node_id = await vector_manager.add_memory(node)
                return [TextContent(type="text", text=f"Guardado con ID: {node_id}")]

            raise ValueError(f"Herramienta {name} no encontrada.")

        except Exception as e:
            logger.error(f"Error en tool {name}: {e}")
            return [TextContent(type="text", text=f"Error: {str(e)}")]