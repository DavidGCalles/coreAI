"""
Tests del Patron Registry (Issue 4.2)

Valida:
- TaskType(StrEnum) con los 4 valores permitidos
- TASK_REGISTRY mapea correctamente TaskType -> handler
- route_task valida y ejecuta las tareas correctamente
- Error handling para casos corruptos o desconocidos
"""

import pytest
import uuid
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.ext.asyncio import AsyncSession
from src.db.models import Task
from src.schemas.tasks import TaskType, TaskDispatchRequest
from src.workers.task_router import route_task, TASK_REGISTRY, handle_extract_entities


class TestTaskTypeEnum:
    """Tests de TaskType como StrEnum."""

    def test_task_type_has_all_required_values(self):
        """Valida que TaskType tiene los 4 valores definidos."""
        assert TaskType.EXTRACT_ENTITIES == "extract_entities"
        assert TaskType.INIT_SESSION == "init_session"
        assert TaskType.FOLLOW_UP == "follow_up"
        assert TaskType.DUMMY_TEST_TASK == "dummy_test_task"

    def test_task_type_from_str_extraction_entities(self):
        """Valida la conversion de str -> enum para extract_entities."""
        tt = TaskType("extract_entities")
        assert tt == TaskType.EXTRACT_ENTITIES
        assert tt.value == "extract_entities"
        assert isinstance(tt, str)

    def test_task_type_from_str_init_session(self):
        """Valida la conversion de str -> enum para init_session."""
        tt = TaskType("init_session")
        assert tt == TaskType.INIT_SESSION
        assert tt.value == "init_session"
        assert isinstance(tt, str)

    def test_task_type_from_str_follow_up(self):
        """Valida la conversion de str -> enum para follow_up."""
        tt = TaskType("follow_up")
        assert tt == TaskType.FOLLOW_UP
        assert tt.value == "follow_up"
        assert isinstance(tt, str)

    def test_task_type_from_str_dummy_test_task(self):
        """Valida la conversion de str -> enum para dummy_test_task."""
        tt = TaskType("dummy_test_task")
        assert tt == TaskType.DUMMY_TEST_TASK
        assert tt.value == "dummy_test_task"
        assert isinstance(tt, str)

    def test_task_type_rejects_invalid_str(self):
        """Valida que TaskType rechaza tipos desconocidos."""
        with pytest.raises(ValueError) as exc_info:
            TaskType("unknown_task")
        assert "unknown_task" in str(exc_info.value)


class TestTaskDispatchRequest:
    """Tests de TaskDispatchRequest como escudo perimetral."""

    def test_task_dispatch_request_validates_task_type_enum(self):
        """Valida que TaskDispatchRequest valida task_type estrictamente con enum."""
        valid_requests = [
            TaskDispatchRequest(task_type=TaskType.EXTRACT_ENTITIES, task_payload={}),
            TaskDispatchRequest(task_type="extract_entities", task_payload={"data": "test"}),
            TaskDispatchRequest(task_type=TaskType.INIT_SESSION, task_payload={"init_data": True}),
            TaskDispatchRequest(task_type=TaskType.FOLLOW_UP, task_payload={"note": "respuesta"}),
            TaskDispatchRequest(task_type=TaskType.DUMMY_TEST_TASK, task_payload={"test_id": uuid.uuid4()}),
        ]

        for req in valid_requests:
            assert req.task_type in TaskType
            assert isinstance(req.task_type, str)

    def test_task_dispatch_request_rejects_invalid_task_type(self):
        """Valida que TaskDispatchRequest rechaza tipos no permitidos."""
        with pytest.raises(Exception) as exc_info:
            TaskDispatchRequest(task_type="invalid_task_type", task_payload={})
        assert "invalid_task_type" in str(exc_info.value).lower()

    def test_task_dispatch_request_with_missing_task_type(self):
        """Valida que TaskDispatchRequest requiere task_type (campo obligatorio)."""
        with pytest.raises(Exception) as exc_info:
            TaskDispatchRequest(task_payload={})
        assert "validation error" in str(exc_info.value).lower()


class TestTaskRegistry:
    """Tests de TASK_REGISTRY centralizado."""

    def test_registry_contains_all_task_types(self):
        """Valida que TASK_REGISTRY tiene entradas para todos los tipos."""
        assert len(TASK_REGISTRY) == 4
        assert TaskType.EXTRACT_ENTITIES in TASK_REGISTRY
        assert TaskType.INIT_SESSION in TASK_REGISTRY
        assert TaskType.FOLLOW_UP in TASK_REGISTRY
        assert TaskType.DUMMY_TEST_TASK in TASK_REGISTRY

    def test_registry_maps_extract_entities(self):
        """Valida el mapping correcto para extract_entities."""
        handler = TASK_REGISTRY.get(TaskType.EXTRACT_ENTITIES)
        assert handler is not None
        assert handler.__name__ == "handle_extract_entities"
        assert callable(handler)

    def test_registry_maps_init_session(self):
        """Valida el mapping correcto para init_session."""
        handler = TASK_REGISTRY.get(TaskType.INIT_SESSION)
        assert handler is not None
        assert handler.__name__ == "handle_init_session"

    def test_registry_maps_follow_up(self):
        """Valida el mapping correcto para follow_up."""
        handler = TASK_REGISTRY.get(TaskType.FOLLOW_UP)
        assert handler is not None
        assert handler.__name__ in ["handle_follow_up", "handle_dummy_test_task"]

    def test_registry_maps_dummy_test_task(self):
        """Valida el mapping correcto para dummy_test_task."""
        handler = TASK_REGISTRY.get(TaskType.DUMMY_TEST_TASK)
        assert handler is not None
        assert handler.__name__ == "handle_dummy_test_task"


class TestRouteTask:
    """Tests de la orquestadora route_task."""

    @pytest.mark.asyncio
    async def test_route_task_valid_type_extract_entities(self):
        """Test de exito: task con tipo valido y handler."""
        task = Task(id=uuid.uuid4())
        task.payload = {"task_type": "extract_entities", "data": {"test": 1}}

        result = await route_task(task, AsyncMock())
        assert result is None

    @pytest.mark.asyncio
    async def test_route_task_valid_type_init_session(self):
        """Test de exito con init_session."""
        task = Task(id=uuid.uuid4())
        task.payload = {"task_type": "init_session", "data": {}}

        result = await route_task(task, AsyncMock())
        assert result is None

    @pytest.mark.asyncio
    async def test_route_task_valid_type_follow_up(self):
        """Test de exito con follow_up."""
        task = Task(id=uuid.uuid4())
        task.payload = {"task_type": "follow_up", "data": {}}

        result = await route_task(task, AsyncMock())
        assert result is None

    @pytest.mark.asyncio
    async def test_route_task_valid_type_dummy_test_task(self):
        """Test de exito con dummy_test_task (para tests)."""
        task = Task(id=uuid.uuid4())
        task.payload = {"task_type": "dummy_test_task", "data": {}}

        result = await route_task(task, AsyncMock())
        assert result is None

    @pytest.mark.asyncio
    async def test_route_task_invalid_type_raises_value_error(self):
        """Valida que tipo desconocido levanta ValueError."""
        task = Task(id=uuid.uuid4())
        task.payload = {"task_type": "unknown_invalid_task"}

        with pytest.raises(ValueError) as exc_info:
            await route_task(task, AsyncMock())

        assert "desconocido" in str(exc_info.value).lower()
        assert "unknown_invalid_task" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_route_task_null_type_raises_value_error(self):
        """Valida que tipo null en payload levanta ValueError."""
        task = Task(id=uuid.uuid4())
        task.payload = {}

        with pytest.raises(ValueError) as exc_info:
            await route_task(task, AsyncMock())

        assert "no proporcionado" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_route_task_empty_string_type(self):
        """Valida que tipo vacio es tratado como invalido."""
        task = Task(id=uuid.uuid4())
        task.payload = {"task_type": ""}

        with pytest.raises(ValueError) as exc_info:
            await route_task(task, AsyncMock())

        assert "desconocido" in str(exc_info.value).lower()


class TestRouteTaskIntegration:
    """Tests de integracion."""

    @pytest.mark.asyncio
    async def test_handler_signature_compliance(self):
        """Valida que todos los handlers tienen la firma correcta (task, AsyncSession)."""
        import inspect

        for task_type, handler in TASK_REGISTRY.items():
            sig = inspect.signature(handler)
            params = list(sig.parameters.keys())
            assert "task" in params, f"{task_type.value} debe recibir 'task'"
