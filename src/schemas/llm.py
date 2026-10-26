from pydantic import BaseModel, Field
from typing import Any

# --- EMBEDDINGS ---
class EmbeddingRequest(BaseModel):
    model: str = Field(..., description="El nombre del modelo enrutado en LiteLLM")
    input: str | list[str] = Field(..., description="El texto o textos a vectorizar")
    modality: str | None = Field(
        None,
        description=(
            "Modalidad del embedding: 'image' para imágenes, None = texto. "
            "Solo viaja al gateway cuando está fijada."
        ),
    )

class EmbeddingUsage(BaseModel):
    prompt_tokens: int
    total_tokens: int

class EmbeddingData(BaseModel):
    embedding: list[float]
    index: int

class EmbeddingResponse(BaseModel):
    data: list[EmbeddingData]
    model: str
    usage: EmbeddingUsage

# --- COMPLETIONS (consolidación de memoria, Issue 4.3 #29) ---
class CompletionRequest(BaseModel):
    """Petición de chat completion vía gateway (grupo 'local-fallback' → LM Studio del host)."""
    model: str = Field(
        ...,
        description="Grupo del modelo enrutado en LiteLLM (p. ej. 'local-fallback').",
    )
    messages: list[dict[str, str]] = Field(
        ...,
        description="Mensajes en formato OpenAI: system/user/assistant.",
    )
    temperature: float = 0.0

class CompletionUsage(BaseModel):
    """Factura de una generación: en chat completions el relleno es obligatorio."""
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

class CompletionChoice(BaseModel):
    """Una elección de respuesta del modelo de chat."""
    message: dict[str, Any] = Field(
        default_factory=dict,
        description="Mensaje del asistente: {'role': 'assistant', 'content': ...}.",
    )
    finish_reason: str | None = None

class CompletionResponse(BaseModel):
    """Respuesta del gateway a una chat completion (formato OpenAI; LM Studio lo respeta)."""
    choices: list[CompletionChoice] = Field(default_factory=list)
    model: str
    usage: CompletionUsage
