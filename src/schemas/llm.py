from pydantic import BaseModel, Field
from typing import Any

# --- EMBEDDINGS ---
class EmbeddingRequest(BaseModel):
    model: str = Field(..., description="El nombre del modelo enrutado en LiteLLM")
    input: str | list[str] = Field(..., description="El texto o textos a vectorizar")

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

# --- COMPLETIONS (Opcional para cuando ataquemos texto) ---
class CompletionRequest(BaseModel):
    model: str
    messages: list[dict[str, str]]
    temperature: float = 0.0