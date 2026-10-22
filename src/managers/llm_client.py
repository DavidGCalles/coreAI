import httpx
import logging
from src.schemas.llm import EmbeddingRequest, EmbeddingResponse
from src.managers.config_manager import config_manager

logger = logging.getLogger(__name__)


class GatewayHTTPError(RuntimeError):
    """
    El proxy LiteLLM respondió 4xx/5xx.

    httpx.HTTPStatusError solo dice «400 Bad Request for url ...» — el
    error_trace del worker quedaría ciego a la causa. El CUERPO de la
    respuesta lleva la causa real (ImageCorruption de Infinity, errores
    del router, ...): esta excepción la propaga recortada para que el
    trace sea un diagnóstico, no un código de estado.
    """


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
                    # exclude_none: la modalidad solo viaja cuando está fijada
                    # (image-embedding) — las requests de texto quedan byte-idénticas.
                    json=request_data.model_dump(exclude_none=True)
                )
                response.raise_for_status()
                
                # Pydantic parsea y valida el JSON de respuesta
                return EmbeddingResponse.model_validate(response.json())
                
            except httpx.HTTPStatusError as e:
                # Trace honesto (#61): el cuerpo del 4xx/5xx del gateway lleva
                # la causa real; str(e) solo lleva el código de estado. Recorte
                # a 600 chars: los cuerpos de LiteLLM arrastran ruido de
                # fallbacks y trazas del router.
                logger.error(f"Error HTTP del proxy LiteLLM: {e.response.status_code} - {e.response.text}")
                raise GatewayHTTPError(
                    f"El proxy LiteLLM devolvió {e.response.status_code} para "
                    f"'{request_data.model}': {e.response.text[:600]}"
                ) from e
            except httpx.RequestError as e:
                logger.error(f"Fallo de conexión física con el proxy: {e}")
                raise
