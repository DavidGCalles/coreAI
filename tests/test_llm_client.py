import pytest
import httpx
from unittest.mock import AsyncMock, patch

from src.schemas.llm import EmbeddingRequest, EmbeddingResponse
from src.managers.llm_client import LLMClient

# Fixture que simula la estructura exacta que escupe LiteLLM (formato OpenAI)
@pytest.fixture
def mock_litellm_payload():
    return {
        "model": "text-embedding",
        "data": [
            {
                "embedding": [0.115, 0.224, -0.332, 0.441],
                "index": 0
            }
        ],
        "usage": {
            "prompt_tokens": 12,
            "total_tokens": 12
        }
    }

@pytest.mark.asyncio
async def test_llm_client_embedding_happy_path(mock_litellm_payload):
    """
    Valida que el cliente formatea correctamente la petición y 
    que Pydantic hidrata la respuesta sin errores de validación.
    """
    client = LLMClient()
    request_data = EmbeddingRequest(model="text-embedding", input="Test de asimilación cognitiva")

    # Interceptamos el context manager y el método POST de httpx
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        # Simulamos una respuesta 200 OK con el JSON estructurado
        mock_response = httpx.Response(
            status_code=200, 
            json=mock_litellm_payload,
            request=httpx.Request("POST", "http://dummy-url")
        )
        mock_post.return_value = mock_response

        # Ejecutamos el cliente
        response = await client.generate_embedding(request_data)

        # 1. Asersiones de comportamiento (¿Envió lo que debía?)
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args.kwargs
        assert "json" in call_kwargs
        assert call_kwargs["json"]["input"] == "Test de asimilación cognitiva"
        assert call_kwargs["json"]["model"] == "text-embedding"

        # 2. Asersiones de contrato (¿Devolvió lo que debía?)
        assert isinstance(response, EmbeddingResponse), "El cliente no devolvió el esquema esperado."
        assert response.model == "text-embedding"
        assert len(response.data) == 1
        assert response.data[0].embedding[0] == 0.115
        assert response.usage.total_tokens == 12

@pytest.mark.asyncio
async def test_llm_client_raises_on_http_error():
    """
    Valida que si Infinity muere y LiteLLM devuelve un 500, 
    el cliente no lo silencia, sino que lo escala violentamente.
    """
    client = LLMClient()
    request_data = EmbeddingRequest(model="text-embedding", input="Fallo inminente")

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_response = httpx.Response(
            status_code=500, 
            content=b"Internal Server Error: Downstream model offline",
            request=httpx.Request("POST", "http://dummy-url")
        )
        mock_post.return_value = mock_response

        # El cliente DEBE lanzar la excepción HTTP de httpx
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await client.generate_embedding(request_data)
        
        assert exc_info.value.response.status_code == 500