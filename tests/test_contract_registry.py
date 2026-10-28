"""
Tests del Registro Central de Contratos de Tarea (ADR-006-001, Issue #43).

Valida:
- validate() acepta/rechaza payload por tipo (vectorize_event, consolidate_memory, vectorize_image_event)
- schema() coincide con model_json_schema() del modelo (cero duplicación)
- list() devuelve el catálogo contratado (stubs excluidos)
- Negativo de frontera: dispatch de un tipo sin contrato → isError explícito
  (no silent pass-through); los tipos contratados siguen despachables.
- ADR-006-003 (#44): el loader vive en la clase del contrato y contract_uri()
  es la única fuente del patrón de URI (placeholder hasta #42).
- Decodificabilidad de imagen en frontera con Pillow (#61, hallazgo E2E 22
  oct): el stub de 99 bytes rebota SÍNCRONO con diagnóstico.
- Descripción textual opcional en el contrato de imagen (BUG-009 #84):
  content legible que persiste en el punto y el recall devuelve; sin él,
  el payload actual valida idéntico (retrocompatible).
"""

import base64
import io

import pytest
from pydantic import ValidationError
from PIL import Image

from src.schemas.tasks import TaskType
from src.contracts import (
    task_contract_registry,
    ContractNotRegisteredError,
    CONTRACT_URI_TEMPLATE,
    ConsolidateMemoryPayload,
    VectorizeEventPayload,
    VectorizeImageEventPayload,
    contract_uri,
)
from src.mcp.tools.task_tools import handle_task_tool


def _data_uri_real(formato: str, mime: str) -> str:
    """Data URI con una imagen RASTER real generada con PIL — la frontera
    exige decodificable, y un fixture inventado ('hello' en bytes) ya no pasa."""
    imagen = Image.new("RGB", (2, 2), (255, 0, 0))
    buffer = io.BytesIO()
    imagen.save(buffer, format=formato)
    return f"data:image/{mime};base64," + base64.b64encode(buffer.getvalue()).decode()


PNG_REAL_DATA_URI = _data_uri_real("PNG", "png")
JPEG_REAL_DATA_URI = _data_uri_real("JPEG", "jpeg")
GIF_REAL_DATA_URI = _data_uri_real("GIF", "gif")
BMP_REAL_DATA_URI = _data_uri_real("BMP", "bmp")
WEBP_REAL_DATA_URI = _data_uri_real("WEBP", "webp")


class TestRegistryValidateVectorizeEvent:
    """validate() acepta/rechaza el contrato de vectorize_event."""

    def test_validate_accepts_canonical_payload(self):
        validated = task_contract_registry.validate(
            TaskType.VECTORIZE_EVENT, {"content": "Memoria canónica del taller."}
        )
        assert validated["content"] == "Memoria canónica del taller."

    def test_validate_normalizes_defaults(self):
        validated = task_contract_registry.validate(
            TaskType.VECTORIZE_EVENT, {"content": "x"}
        )
        assert validated["domain"] == "system"
        assert validated["visibility"] == "private"

    def test_validate_accepts_explicit_domain_and_visibility(self):
        validated = task_contract_registry.validate(
            TaskType.VECTORIZE_EVENT,
            {"content": "x", "domain": "conversation", "visibility": "shared"},
        )
        assert validated["domain"] == "conversation"
        assert validated["visibility"] == "shared"

    def test_validate_rejects_missing_content(self):
        with pytest.raises(ValidationError):
            task_contract_registry.validate(
                TaskType.VECTORIZE_EVENT, {"domain": "system"}
            )

    def test_validate_rejects_empty_content(self):
        with pytest.raises(ValidationError):
            task_contract_registry.validate(TaskType.VECTORIZE_EVENT, {"content": ""})

    def test_validate_rejects_extra_keys(self):
        """El contrato canónico mata la extracción resiliente (claves candidatas fantasma)."""
        with pytest.raises(ValidationError):
            task_contract_registry.validate(
                TaskType.VECTORIZE_EVENT,
                {"content": "x", "summary_content": "clave candidata fantasma"},
            )


class TestRegistryValidateConsolidateMemory:
    """validate() exige domain y time_window en consolidate_memory."""

    def test_validate_accepts_full_payload(self):
        validated = task_contract_registry.validate(
            TaskType.CONSOLIDATE_MEMORY, {"domain": "conversation", "time_window": "7d"}
        )
        assert validated == {"domain": "conversation", "time_window": "7d"}

    def test_validate_rejects_missing_domain(self):
        with pytest.raises(ValidationError):
            task_contract_registry.validate(
                TaskType.CONSOLIDATE_MEMORY, {"time_window": "7d"}
            )

    def test_validate_rejects_missing_time_window(self):
        with pytest.raises(ValidationError):
            task_contract_registry.validate(
                TaskType.CONSOLIDATE_MEMORY, {"domain": "conversation"}
            )

    def test_validate_rejects_empty_time_window(self):
        with pytest.raises(ValidationError):
            task_contract_registry.validate(
                TaskType.CONSOLIDATE_MEMORY, {"domain": "conversation", "time_window": ""}
            )


class TestRegistryValidateVectorizeImageEvent:
    """validate() acepta/rechaza el contrato de vectorize_image_event (ADR-007-003, #61).

    Imagen como data URI base64 RASTER-ONLY (decisión 22 oct hecha contrato)
    y evento AUTO-ANCLADO (rework 22 oct): SIN text_event_id — la correlación
    texto↔imagen la da la sesión. project (FR-3 #51) viaja en el propio
    evento, no en un registro textual.
    """

    def test_validate_accepts_canonical_payload(self):
        """Canon mínimo: image como data URI REAL decodificable; defaults materializados."""
        validated = task_contract_registry.validate(
            TaskType.VECTORIZE_IMAGE_EVENT,
            {"image": PNG_REAL_DATA_URI},
        )
        assert validated == {
            "image": PNG_REAL_DATA_URI,
            "content": None,
            "project": None,
            "domain": "system",
            "visibility": "private",
        }

    def test_validate_accepts_content(self):
        """BUG-009 (#84): descripción textual legible opcional — mimetiza el
        contrato del evento de texto. Qué texto mete y quién lo dice es del
        edge client del inquilino: aquí solo se transporta."""
        validated = task_contract_registry.validate(
            TaskType.VECTORIZE_IMAGE_EVENT,
            {"image": PNG_REAL_DATA_URI, "content": "Catalepsis de la pieza 7"},
        )
        assert validated["content"] == "Catalepsis de la pieza 7"

    def test_validate_rejects_empty_content(self):
        """Content opcional pero no vacío: "" no es una descripción legible."""
        with pytest.raises(ValidationError):
            task_contract_registry.validate(
                TaskType.VECTORIZE_IMAGE_EVENT,
                {"image": PNG_REAL_DATA_URI, "content": ""},
            )

    def test_validate_accepts_explicit_project(self):
        """FR-3 (#51): el proyecto viaja en el propio contrato de imagen."""
        validated = task_contract_registry.validate(
            TaskType.VECTORIZE_IMAGE_EVENT,
            {"image": PNG_REAL_DATA_URI, "project": "elios-workshop"},
        )
        assert validated["project"] == "elios-workshop"

    def test_validate_rejects_empty_project(self):
        with pytest.raises(ValidationError):
            task_contract_registry.validate(
                TaskType.VECTORIZE_IMAGE_EVENT,
                {"image": PNG_REAL_DATA_URI, "project": ""},
            )

    def test_validate_rejects_missing_image(self):
        with pytest.raises(ValidationError):
            task_contract_registry.validate(TaskType.VECTORIZE_IMAGE_EVENT, {})

    def test_validate_rejects_path_instead_of_data_uri(self):
        """Un path local NO viaja: el payload es autocontenido (sistema distribuido)."""
        with pytest.raises(ValidationError):
            task_contract_registry.validate(
                TaskType.VECTORIZE_IMAGE_EVENT,
                {"image": "/home/dev/coreAI/scratch/gatos_coco.jpg"},
            )

    def test_validate_rejects_malformed_data_uri(self):
        """data: sin base64 o sin tipo image/* no pasa la frontera de forma."""
        with pytest.raises(ValidationError):
            task_contract_registry.validate(
                TaskType.VECTORIZE_IMAGE_EVENT,
                {"image": "data:text/plain;base64,aGVsbG8="},
            )

    def test_validate_rejects_svg_data_uri(self):
        """Semántica anti-SVG (22 oct) hecha CONTRATO: un SVG no produce
        embedding numérico (PIL no lo lee; moriría asíncrono en Infinity con
        ImageCorruption) — rebota en la frontera, no en el lane."""
        with pytest.raises(ValidationError):
            task_contract_registry.validate(
                TaskType.VECTORIZE_IMAGE_EVENT,
                {"image": "data:image/svg+xml;base64,PHN2ZyB4bWxucz0ibj0iLzIiPjwvc3ZnPg=="},
            )

    def test_validate_rejects_empty_base64_body(self):
        """Un data URI sin contenido no es una imagen: rebota en la frontera,
        no muere asíncrona en Infinity."""
        with pytest.raises(ValidationError):
            task_contract_registry.validate(
                TaskType.VECTORIZE_IMAGE_EVENT,
                {"image": "data:image/png;base64,"},
            )

    def test_validate_rejects_non_base64_body(self):
        """El cuerpo tras «base64,» es alfabeto estricto anclado al final:
        basura tras la coma rebota ANTES de encolarse."""
        with pytest.raises(ValidationError):
            task_contract_registry.validate(
                TaskType.VECTORIZE_IMAGE_EVENT,
                {"image": "data:image/png;base64,aGVsbG8!!esto no es base64"},
            )

    def test_validate_rejects_text_event_id_as_ghost_key(self):
        """text_event_id es clave fantasma: el evento de imagen es auto-anclado
        (la correlación texto↔imagen la da la sesión, no otra tarea)."""
        with pytest.raises(ValidationError):
            task_contract_registry.validate(
                TaskType.VECTORIZE_IMAGE_EVENT,
                {
                    "image": PNG_REAL_DATA_URI,
                    "text_event_id": "11111111-1111-1111-1111-111111111111",
                },
            )


class TestFrontierValidaDecodificabilidad:
    """Decodificabilidad de imagen en frontera con Pillow (#61, hallazgo E2E 22 oct).

    La tarea 1fefde4a encoló un stub de 99 BYTES con cabecera JPEG válida
    (uri_len=155: 23 de prefijo + 132 de base64) y murió asíncrona en
    Infinity con ImageCorruption. [DAVID] La frontera valida ahora con el
    MISMO oráculo que el consumidor (infinity_emb decodifica con PIL): los
    stubs truncados rebotan SÍNCRONO en el dispatch, con diagnóstico de
    bytes, y el agente se autocorrige en el acto (patrón ADR-006).
    """

    @pytest.mark.parametrize(
        "uri",
        [PNG_REAL_DATA_URI, JPEG_REAL_DATA_URI, GIF_REAL_DATA_URI, BMP_REAL_DATA_URI, WEBP_REAL_DATA_URI],
        ids=["png", "jpeg", "gif", "bmp", "webp"],
    )
    def test_formats_raster_reales_pasan(self, uri):
        """Los cinco formatos del contrato, con imágenes reales de PIL, pasan."""
        validated = task_contract_registry.validate(
            TaskType.VECTORIZE_IMAGE_EVENT, {"image": uri}
        )
        assert validated["image"] == uri

    def test_stub_truncado_de_99_bytes_rebota(self):
        """LA clase del hallazgo: JPEG real cortado a 99 bytes (el veredicto
        del JSONB — cabecera válida, sin final del archivo). Rebota CON el
        diagnóstico de bytes que delata al truncador."""
        raw = base64.b64decode(JPEG_REAL_DATA_URI.partition(",")[2])
        assert len(raw) > 99  # fixture válida para el corte
        stub = raw[:99]
        uri = "data:image/jpeg;base64," + base64.b64encode(stub).decode()

        with pytest.raises(ValidationError) as exc_info:
            task_contract_registry.validate(
                TaskType.VECTORIZE_IMAGE_EVENT, {"image": uri}
            )
        mensaje = str(exc_info.value)
        assert "99 bytes" in mensaje
        assert ("truncad" in mensaje.lower()) or ("decodificable" in mensaje.lower())

    def test_png_truncado_rebota(self):
        """PNG cortado por la mitad: load() revienta en frontera, no en el lane."""
        raw = base64.b64decode(PNG_REAL_DATA_URI.partition(",")[2])
        uri = "data:image/png;base64," + base64.b64encode(raw[: len(raw) // 2]).decode()
        with pytest.raises(ValidationError) as exc_info:
            task_contract_registry.validate(
                TaskType.VECTORIZE_IMAGE_EVENT, {"image": uri}
            )
        assert "bytes decodificados" in str(exc_info.value)

    def test_mime_mentiroso_rebota(self):
        """PNG real declarado como JPEG: el formato debe casar con el mime."""
        with pytest.raises(ValidationError) as exc_info:
            task_contract_registry.validate(
                TaskType.VECTORIZE_IMAGE_EVENT,
                {"image": PNG_REAL_DATA_URI.replace("image/png", "image/jpeg")},
            )
        assert "no casa" in str(exc_info.value)

    def test_texto_como_imagen_rebota(self):
        """El viejo fixture 'aGVsbG8=' ('hello' en bytes) ya no pasa: no es una imagen."""
        with pytest.raises(ValidationError) as exc_info:
            task_contract_registry.validate(
                TaskType.VECTORIZE_IMAGE_EVENT,
                {"image": "data:image/png;base64,aGVsbG8="},
            )
        assert "no son una imagen raster decodificable" in str(exc_info.value)

    def test_base64_longitud_invalida_rebota(self):
        """Cuerpo que no es múltiplo de 4: el patrón textual lo admite, el
        decodificador estricto no."""
        with pytest.raises(ValidationError):
            task_contract_registry.validate(
                TaskType.VECTORIZE_IMAGE_EVENT,
                {"image": "data:image/png;base64,A"},
            )


class TestRegistrySchemaAndList:
    """schema() coincide con el modelo; list() devuelve el catálogo contratado."""

    def test_schema_matches_model_json_schema(self):
        """La misma definición que valida es la que se sirve: cero duplicación."""
        assert task_contract_registry.schema(TaskType.VECTORIZE_EVENT) == VectorizeEventPayload.model_json_schema()
        assert task_contract_registry.schema(TaskType.CONSOLIDATE_MEMORY) == ConsolidateMemoryPayload.model_json_schema()
        assert task_contract_registry.schema(TaskType.VECTORIZE_IMAGE_EVENT) == VectorizeImageEventPayload.model_json_schema()

    def test_list_returns_only_contracted_catalog(self):
        """Solo los tipos con lógica real; los stubs no son despachables."""
        assert set(task_contract_registry.list()) == {
            "vectorize_event",
            "consolidate_memory",
            "vectorize_image_event",
        }

    def test_schema_of_unregistered_type_raises(self):
        with pytest.raises(ContractNotRegisteredError):
            task_contract_registry.schema(TaskType.DUMMY_TEST_TASK)

    def test_validate_of_unregistered_type_raises(self):
        with pytest.raises(ContractNotRegisteredError):
            task_contract_registry.validate(TaskType.INIT_SESSION, {})


class TestDispatchFrontierContracts:
    """Negativo de frontera (ADR-006-001): sin contrato no hay despacho."""

    @pytest.mark.asyncio
    async def test_dispatch_stub_type_returns_explicit_error(self):
        for stub_type in ("init_session", "dummy_test_task", "extract_entities", "follow_up"):
            response = await handle_task_tool(
                "coreai_dispatch_task",
                {"task_type": stub_type, "task_payload": {}},
            )
            assert response["isError"] is True, f"{stub_type} pasó la frontera sin contrato"
            text = response["content"][0]["text"]
            assert stub_type in text
            assert "contrato" in text.lower()
            # El catálogo contratado viaja en el mensaje (autocorrección del agente)
            assert "vectorize_event" in text
            assert "consolidate_memory" in text

    def test_contracted_types_are_dispatchable(self):
        """Los tipos con lógica real tienen contrato: la frontera los deja pasar.

        La persistencia E2E de un tipo contratado la valida el test de integración
        migrado (test_mcp_task_tools) con su limpieza de BD correspondiente.
        """
        assert task_contract_registry.has_contract(TaskType.VECTORIZE_EVENT)
        assert task_contract_registry.has_contract(TaskType.CONSOLIDATE_MEMORY)
        assert task_contract_registry.has_contract(TaskType.VECTORIZE_IMAGE_EVENT)


class TestContractLoader:
    """ADR-006-003 (#44): el loader vive en la clase del contrato, no en la frontera."""

    def test_load_returns_normalized_dict(self):
        loaded = VectorizeEventPayload.load({"content": "x"})
        assert loaded == {
            "content": "x",
            "project": None,
            "domain": "system",
            "visibility": "private",
        }

    def test_load_rejects_violating_payload(self):
        with pytest.raises(ValidationError):
            VectorizeEventPayload.load({"purpose": "clave inventada"})

    def test_registry_validate_delegates_to_loader(self):
        """validate() del registro es una delegación fina sobre load() del contrato."""
        assert task_contract_registry.validate(
            TaskType.VECTORIZE_EVENT, {"content": "x"}
        ) == VectorizeEventPayload.load({"content": "x"})


class TestContractUri:
    """contract_uri(): única fuente del patrón de URI (placeholder hasta #42)."""

    def test_uri_accepts_enum_and_string(self):
        assert contract_uri(TaskType.VECTORIZE_EVENT) == "coreai://contracts/tasks/vectorize_event"
        assert contract_uri("consolidate_memory") == "coreai://contracts/tasks/consolidate_memory"

    def test_uri_matches_template(self):
        assert contract_uri(TaskType.CONSOLIDATE_MEMORY) == CONTRACT_URI_TEMPLATE.format(
            task_type="consolidate_memory"
        )

