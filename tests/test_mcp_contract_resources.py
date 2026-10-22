"""
Tests de la exposición de Contratos como MCP Resources (ADR-006-002, Issue #42).

Valida:
- resources/list publica la raíz del catálogo + un resource por tipo contratado
  (URIs == contract_uri(); catálogo listado == registry.list()).
- resources/read de la raíz → índice (tipos, descripciones, URIs) == catalog().
- resources/read por tipo → JSON Schema == registry.schema() (DoD: lo que lee
  el cliente es lo que validará el dispatch — misma fuente de verdad).
- Negativos de protocolo: URI fuera del espacio, tipo inexistente y tipo sin
  contrato → MCPError -32002 explícito, no crash ni contenido vacío.

La comprobación manual en el MCP Inspector (DoD del issue) se documenta en
el propio issue #42.
"""

import json
import pytest

from mcp.shared.exceptions import MCPError

from src.schemas.tasks import TaskType
from src.contracts import (
    CONTRACT_URI_TEMPLATE,
    ConsolidateMemoryPayload,
    VectorizeEventPayload,
    VectorizeImageEventPayload,
    contract_uri,
    task_contract_registry,
)
from src.mcp.resources.contract_resources import (
    CATALOG_MIME_TYPE,
    CONTRACTS_ROOT_URI,
    CONTRACT_MIME_TYPE,
    get_contract_resources,
    read_contract_resource,
)

MODEL_BY_TYPE = {
    "vectorize_event": VectorizeEventPayload,
    "consolidate_memory": ConsolidateMemoryPayload,
    "vectorize_image_event": VectorizeImageEventPayload,
}


class TestResourcesList:
    """resources/list: la raíz del catálogo + un resource por tipo contratado."""

    def test_list_includes_navigable_root(self):
        resources = get_contract_resources()
        roots = [r for r in resources if str(r.uri) == CONTRACTS_ROOT_URI]
        assert len(roots) == 1, "Debe haber exactamente una raíz del catálogo"
        root = roots[0]
        assert root.mime_type == CATALOG_MIME_TYPE
        assert root.description  # el índice se explica a sí mismo

    def test_list_publishes_one_resource_per_contracted_type(self):
        resources = get_contract_resources()
        by_uri = {str(r.uri): r for r in resources}
        for task_type in task_contract_registry.list():
            uri = contract_uri(task_type)
            assert uri in by_uri, f"Falta el resource de {task_type}"
            resource = by_uri[uri]
            assert uri == CONTRACT_URI_TEMPLATE.format(task_type=task_type)
            assert resource.mime_type == CONTRACT_MIME_TYPE
            assert task_type in resource.name
            assert resource.description

    def test_list_matches_registry_catalog(self):
        """El catálogo listado == registry.list(): misma fuente de verdad."""
        resources = get_contract_resources()
        listed_types = {
            str(r.uri).rsplit("/", 1)[-1]
            for r in resources
            if str(r.uri) != CONTRACTS_ROOT_URI
        }
        assert listed_types == set(task_contract_registry.list())


class TestReadRoot:
    """resources/read sobre la raíz → índice (modelo mental REST previsto)."""

    @pytest.mark.asyncio
    async def test_read_root_returns_catalog_index(self):
        result = await read_contract_resource(CONTRACTS_ROOT_URI)
        assert len(result.contents) == 1
        content = result.contents[0]
        assert str(content.uri) == CONTRACTS_ROOT_URI
        assert content.mime_type == CATALOG_MIME_TYPE

        index = json.loads(content.text)
        assert index["catalog"] == CONTRACTS_ROOT_URI
        assert index["contract_uri_template"] == CONTRACT_URI_TEMPLATE
        assert index["contracts"] == task_contract_registry.catalog()

    @pytest.mark.asyncio
    async def test_every_uri_in_the_index_is_resolvable(self):
        """DoD de navegabilidad: cada URI que publica el índice se puede leer."""
        result = await read_contract_resource(CONTRACTS_ROOT_URI)
        index = json.loads(result.contents[0].text)
        for entry in index["contracts"]:
            schema_result = await read_contract_resource(entry["uri"])
            assert len(schema_result.contents) == 1


class TestReadContractByType:
    """resources/read por tipo: la aserción de igualdad del DoD."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("task_type", sorted(task_contract_registry.list()))
    async def test_read_returns_the_schema_the_dispatch_validates(self, task_type):
        """Lo que lee el cliente == lo que validará el dispatch (fuente única)."""
        result = await read_contract_resource(contract_uri(task_type))
        assert len(result.contents) == 1
        content = result.contents[0]
        assert content.mime_type == CONTRACT_MIME_TYPE

        # Igualdad contra el registro (la superficie que usa la frontera)...
        assert json.loads(content.text) == task_contract_registry.schema(
            TaskType(task_type)
        )
        # ...y contra el modelo del contrato (cero duplicación).
        assert json.loads(content.text) == MODEL_BY_TYPE[task_type].model_json_schema()

    @pytest.mark.asyncio
    async def test_read_schema_embeds_field_descriptions(self):
        """El schema viaja con las descripciones de los campos: contrato autónomo."""
        result = await read_contract_resource(contract_uri("vectorize_event"))
        schema = json.loads(result.contents[0].text)
        assert "content" in schema["properties"]
        assert schema["properties"]["content"]["description"]


class TestReadErrors:
    """Negativos de protocolo: error explícito, no crash ni contenido vacío."""

    @pytest.mark.asyncio
    async def test_uri_outside_contract_namespace(self):
        with pytest.raises(MCPError) as exc_info:
            await read_contract_resource("coreai://other/thing")
        assert exc_info.value.error.code == -32002
        assert CONTRACTS_ROOT_URI in exc_info.value.error.message

    @pytest.mark.asyncio
    async def test_unknown_task_type(self):
        with pytest.raises(MCPError) as exc_info:
            await read_contract_resource(CONTRACTS_ROOT_URI + "/banana")
        assert exc_info.value.error.code == -32002
        assert "banana" in exc_info.value.error.message
        # El catálogo contratado viaja en el error (autocorrección del agente)
        assert "vectorize_event" in exc_info.value.error.message

    @pytest.mark.asyncio
    async def test_stub_type_without_contract(self):
        """Un TaskType real pero SIN contrato (init_session) también es -32002."""
        with pytest.raises(MCPError) as exc_info:
            await read_contract_resource(contract_uri(TaskType.INIT_SESSION))
        assert exc_info.value.error.code == -32002
        assert "tiene contrato registrado" in exc_info.value.error.message

    @pytest.mark.asyncio
    async def test_missing_uri_is_invalid_params(self):
        with pytest.raises(MCPError) as exc_info:
            await read_contract_resource(None)
        assert exc_info.value.error.code == -32602

    @pytest.mark.asyncio
    async def test_uri_with_trailing_segment_is_not_resolvable(self):
        with pytest.raises(MCPError) as exc_info:
            await read_contract_resource(contract_uri("vectorize_event") + "/extra")
        assert exc_info.value.error.code == -32002
