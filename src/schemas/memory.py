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
    tags: list[str] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)
    source_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

class CoreMemoryNode(BaseModel):
    """Representación atómica de un vector a persistir en Qdrant."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(default_factory=uuid4)
    tenant_id: str = Field(...)
    domain: DomainType
    content: str = Field(...)
    visibility: Visibility = Field(default=Visibility.PRIVATE)
    metadata: MemoryMetadata = Field(default_factory=MemoryMetadata)

class MemorySearchFilters(BaseModel):
    """Parámetros de entrada para consultas al motor vectorial."""
    tenant_id: str = Field(..., description="ID del propietario para aislamiento de datos.")
    query: str = Field(..., description="Texto en lenguaje natural para la búsqueda semántica.")
    domain: DomainType | None = Field(None, description="Filtrar por un dominio específico.")
    tags_any: list[str] | None = Field(None, description="OR: contiene alguna de estas etiquetas.")
    tags_all: list[str] | None = Field(None, description="AND: contiene todas estas etiquetas.")
    properties_match: dict[str, Any] | None = Field(None, description="Filtro exacto por propiedades clave-valor.")
    limit: int = Field(default=5, ge=1, le=50)
    score_threshold: float = Field(default=0.7, ge=0.0, le=1.0)