import httpx
import logging
from src.schemas.llm import EmbeddingRequest, EmbeddingResponse
from src.managers.config_manager import config_manager

logger = logging.getLogger(__name__)

class LLMClient:
    """
    Cliente tonto de I/O para comunicarse con el proxy LiteLLM.
    La validación recae estrictamente en Pydantic.
    """
    def __init__(self):
        config = config_manager.get_litellm_config()
        self.base_url = config["url"]
        self.headers = {
            "Authorization": f"Bearer {config['api_key']}",
            "Content-Type": "application/json"
        }
        # El timeout es vital para no colgar a los workers si Infinity muere
        self.timeout = httpx.Timeout(10.0, read=30.0)

    async def generate_embedding(self, request_data: EmbeddingRequest) -> EmbeddingResponse:
        """
        Envía la petición validada al proxy y devuelve una respuesta estructurada.
        """
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.post(
                    f"{self.base_url}/v1/embeddings",
                    headers=self.headers,
                    json=request_data.model_dump() # Pydantic se encarga
                )
                response.raise_for_status()
                
                # Pydantic parsea y valida el JSON de respuesta
                return EmbeddingResponse.model_validate(response.json())
                
            except httpx.HTTPStatusError as e:
                logger.error(f"Error HTTP del proxy LiteLLM: {e.response.status_code} - {e.response.text}")
                raise
            except httpx.RequestError as e:
                logger.error(f"Fallo de conexión física con el proxy: {e}")
                raise