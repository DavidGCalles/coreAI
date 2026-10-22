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
"""

import pytest
from pydantic import ValidationError

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
    """validate() acepta/rechaza el contrato de vectorize_image_event (ADR-007-003, #61)."""

    def test_validate_accepts_canonical_payload(self):
        validated = task_contract_registry.validate(
            TaskType.VECTORIZE_IMAGE_EVENT,
            {
                "image": "data:image/png;base64,aGVsbG8=",
                "text_event_id": "11111111-1111-1111-1111-111111111111",
            },
        )
        assert validated["image"] == "data:image/png;base64,aGVsbG8="
        assert validated["text_event_id"] == "11111111-1111-1111-1111-111111111111"

    def test_validate_normalizes_defaults(self):
        validated = task_contract_registry.validate(
            TaskType.VECTORIZE_IMAGE_EVENT,
            {
                "image": "data:image/jpeg;base64,aGVsbG8=",
                "text_event_id": "11111111-1111-1111-1111-111111111111",
            },
        )
        assert validated["domain"] == "system"
        assert validated["visibility"] == "private"

    def test_validate_rejects_missing_image(self):
        with pytest.raises(ValidationError):
            task_contract_registry.validate(
                TaskType.VECTORIZE_IMAGE_EVENT,
                {"text_event_id": "11111111-1111-1111-1111-111111111111"},
            )

    def test_validate_rejects_missing_text_event_id(self):
        with pytest.raises(ValidationError):
            task_contract_registry.validate(
                TaskType.VECTORIZE_IMAGE_EVENT, {"image": "data:image/png;base64,aGVsbG8="}
            )

    def test_validate_rejects_extra_keys(self):
        """El proyecto NO viaja en el contrato de imagen (FR-3): vive en el registro textual."""
        with pytest.raises(ValidationError):
            task_contract_registry.validate(
                TaskType.VECTORIZE_IMAGE_EVENT,
                {
                    "image": "data:image/png;base64,aGVsbG8=",
                    "text_event_id": "11111111-1111-1111-1111-111111111111",
                    "project": "clave fantasma — el link viaja en el registro textual",
                },
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
