from enum import StrEnum
from uuid import UUID, uuid4
from datetime import datetime, UTC
from pydantic import BaseModel, Field, ConfigDict
from typing import Any

class DomainType(StrEnum):
    """Clasificación estructural del nodo vectorial."""
    DOCUMENT = "document"
    CONVERSATION = "conversation"
    SYSTEM = "system"

class Visibility(StrEnum):
    """Niveles de control de acceso para multitenencia."""
    PRIVATE = "private"
    SHARED = "shared"
    PUBLIC = "public"

class MemoryMetadata(BaseModel):
    """Metadatos indexables para filtrado avanzado en el motor vectorial."""
    tags: list[str] = Field(default_factory=list, description="Etiquetas para filtrado inclusivo o exclusivo.")
    properties: dict[str, Any] = Field(
        default_factory=dict, 
        description="Pares clave-valor arbitrarios para filtrado por payload en Qdrant."
    )
    source_id: str | None = Field(None, description="Identificador del origen (hash, URI, ID externo).")
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

class CoreMemoryNode(BaseModel):
    """Representación atómica de un vector a persistir en Qdrant."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(default_factory=uuid4)
    tenant_id: str = Field(..., description="Identificador de aislamiento del cliente/propietario.")
    domain: DomainType
    content: str = Field(..., description="Texto crudo objetivo de la vectorización.")
    visibility: Visibility = Field(default=Visibility.PRIVATE)
    metadata: MemoryMetadata = Field(default_factory=MemoryMetadata)

class MemorySearchFilters(BaseModel):
    """Parámetros de entrada para consultas al motor vectorial."""
    tenant_id: str
    domain: DomainType | None = None
    tags_any: list[str] | None = Field(None, description="Coincidencia con al menos una etiqueta (operador OR).")
    tags_all: list[str] | None = Field(None, description="Coincidencia con todas las etiquetas (operador AND).")
    properties_match: dict[str, Any] | None = Field(None, description="Coincidencia exacta de pares clave-valor en properties.")
    limit: int = Field(default=5, ge=1, le=50)
    score_threshold: float = Field(default=0.7, ge=0.0, le=1.0)