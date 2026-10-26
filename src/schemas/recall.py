"""
Schemas planos del recall síncrono (ADR-005-002).

Regla de superficie (decisión 26 oct): schemas planos y single-purpose —
sin uniones ni estructuras anidadas, que confunden a los modelos (misma
razón que los contratos como resources). Higiene de superficies: nada de
numeración interna (ADR/FR/issues) en lo que ven los clientes.
"""

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.contracts.registry import _validar_imagen_raster


class MemoryHit(BaseModel):
    """Un resultado de recall: plano — score, dominio y las claves planas del punto."""
    point_id: str = Field(..., description="UUID del punto en el vector store.")
    domain: str = Field(..., description="Colección de origen: document, conversation, system o images.")
    score: float = Field(..., description="Similitud por coseno con la consulta.")
    content: str | None = Field(None, description="Contenido textual del punto (los puntos de imagen no lo llevan).")
    session_id: str | None = Field(None, description="Sesión del evento de imagen (correlación texto↔imagen).")
    project: str | None = Field(None, description="Proyecto del registro, si lo trae el punto.")


class RecallTextRequest(BaseModel):
    """Búsqueda semántica read-only en la memoria de texto."""
    model_config = ConfigDict(extra="forbid")

    query: str = Field(..., min_length=1, description="Consulta en lenguaje natural.")
    tenant_id: str = Field(..., min_length=1, description="ID del inquilino: aislamiento obligatorio de los datos.")
    project: str | None = Field(None, min_length=1, description="Solo puntos registrados bajo este proyecto. Opcional.")
    top_k: int = Field(5, ge=1, le=50, description="Máximo número de resultados tras el merge global.")
    score_threshold: float = Field(0.7, ge=0.0, le=1.0, description="Similitud mínima para devolver un resultado; por debajo: no-match honesto.")


class RecallImageRequest(BaseModel):
    """Búsqueda por similitud visual contra la memoria de imágenes (identify)."""
    model_config = ConfigDict(extra="forbid")

    image: str = Field(
        ...,
        pattern=r"^data:image/(?:jpeg|jpg|png|webp|gif|bmp);base64,[A-Za-z0-9+/]+={0,2}$",
        description=(
            "Imagen de consulta como data URI base64 (jpeg|jpg|png|webp|gif|bmp). "
            "Raster: los SVG no son válidos. La imagen de consulta NO se "
            "persiste: embed efímero, sin tasks, sesión limpia."
        ),
    )
    tenant_id: str = Field(..., min_length=1, description="ID del inquilino: aislamiento obligatorio de los datos.")
    project: str | None = Field(None, min_length=1, description="Solo puntos registrados bajo este proyecto. Opcional.")
    top_k: int = Field(5, ge=1, le=50, description="Máximo número de resultados.")
    score_threshold: float = Field(
        0.5,
        ge=0.0,
        le=1.0,
        description="Similitud mínima para un match; por debajo: no-match honesto. Los cosenos de visión corren más bajos que los de texto.",
    )

    @field_validator("image")
    @classmethod
    def _imagen_decodificable(cls, value: str) -> str:
        """Validación hermana del contrato de ingesta: el mismo oráculo Pillow."""
        problema = _validar_imagen_raster(value)
        if problema is not None:
            raise ValueError(
                f"{problema}. Relee el archivo completo y envía su base64 íntegra."
            )
        return value


class RecallResponse(BaseModel):
    """Respuesta del recall: hits planos ordenados por score; vacío = no-match honesto bajo umbral."""
    hits: list[MemoryHit] = Field(default_factory=list)
