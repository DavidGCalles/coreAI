"""
Registro Central de Contratos de Tarea — ADR-006-001 (#43).

Única fuente de verdad de los contratos de payload por TaskType. Superficies:
validate() (delega en el load() del contrato), schema(), list(), contract_uri(),
catalog(). Regla estructural: ningún tipo es despachable sin contrato registrado.

Racional y decisiones: ADR-006 (docs/adr/006-mcp-contracts.md) y PR #52.
"""

import inspect

from pydantic import BaseModel, ConfigDict, Field
from typing import Any

from src.schemas.tasks import TaskType
from src.schemas.memory import DomainType, Visibility

CONTRACT_URI_TEMPLATE = "coreai://contracts/tasks/{task_type}"


def contract_uri(task_type: "TaskType | str") -> str:
    """URI del resource del contrato (ADR-006-002)."""
    value = task_type.value if isinstance(task_type, TaskType) else str(task_type)
    return CONTRACT_URI_TEMPLATE.format(task_type=value)


class ContractNotRegisteredError(Exception):
    """El TaskType solicitado no tiene contrato en el registro."""


class TaskPayloadContract(BaseModel):
    """Base de los contratos de payload de tarea (ADR-006): valida y normaliza."""

    model_config = ConfigDict(extra="forbid")

    @classmethod
    def load(cls, payload: dict[str, Any]) -> dict[str, Any]:
        """Valida un payload crudo y devuelve el dict normalizado (defaults materializados).

        Raises:
            pydantic.ValidationError: el payload viola el contrato.
        """
        return cls.model_validate(payload).model_dump(mode="json")


class VectorizeEventPayload(TaskPayloadContract):
    """Contrato de `vectorize_event` (Issue 4.3)."""

    content: str = Field(
        ...,
        min_length=1,
        description="Texto canónico a vectorizar. Clave obligatoria y única de contenido.",
    )
    domain: DomainType = Field(
        default=DomainType.SYSTEM,
        description="Dominio del nodo de memoria. Por defecto 'system'.",
    )
    visibility: Visibility = Field(
        default=Visibility.PRIVATE,
        description="Nivel de acceso del nodo de memoria. Por defecto 'private'.",
    )


class ConsolidateMemoryPayload(TaskPayloadContract):
    """Contrato de `consolidate_memory`."""

    domain: DomainType = Field(
        ...,
        description="Dominio cuya ventana temporal se consolida. Obligatorio.",
    )
    time_window: str = Field(
        ...,
        min_length=1,
        description="Ventana temporal de consolidación (p. ej. '7d'). Obligatorio.",
    )


class TaskContractRegistry:
    """Fuente única de verdad de los contratos de tarea."""

    def __init__(self) -> None:
        self._contracts: dict[TaskType, type[TaskPayloadContract]] = {}

    def register(self, task_type: TaskType, contract: type[TaskPayloadContract]) -> None:
        """Da de alta el contrato de un TaskType (idempotente)."""
        self._contracts[task_type] = contract

    def has_contract(self, task_type: TaskType) -> bool:
        """Indica si el tipo tiene contrato registrado (despachable)."""
        return task_type in self._contracts

    def validate(self, task_type: TaskType, payload: dict[str, Any]) -> dict[str, Any]:
        """Valida y normaliza el payload contra el contrato del tipo.

        Raises:
            ContractNotRegisteredError: el tipo no tiene contrato.
            pydantic.ValidationError: el payload viola el contrato.
        """
        return self._require(task_type).load(payload)

    def schema(self, task_type: TaskType) -> dict[str, Any]:
        """JSON Schema del contrato."""
        return self._require(task_type).model_json_schema()

    def list(self) -> list[str]:
        """Catálogo de tipos contratados."""
        return sorted(task_type.value for task_type in self._contracts)

    def catalog(self) -> list[dict[str, str]]:
        """Catálogo navegable de contratos (ADR-006-002, #42): tipo, URI y descripción.

        Superficie de descubrimiento: alimenta resources/list y la lectura de
        la raíz coreai://contracts/tasks. Misma fuente de verdad que validate()
        y schema(): se construye iterando _contracts, sin duplicación alguna.
        """
        entries: list[dict[str, str]] = []
        for task_type, contract in sorted(self._contracts.items()):
            entries.append(
                {
                    "task_type": task_type.value,
                    "uri": contract_uri(task_type),
                    "description": (inspect.getdoc(contract) or "").strip(),
                }
            )
        return entries

    def _require(self, task_type: TaskType) -> type[TaskPayloadContract]:
        contract = self._contracts.get(task_type)
        if contract is None:
            raise ContractNotRegisteredError(
                f"El tipo de tarea '{task_type}' no tiene contrato registrado. "
                f"Sin contrato no hay despacho (ADR-006-001). "
                f"Tipos contratados: {', '.join(self.list()) or 'ninguno'}."
            )
        return contract


task_contract_registry = TaskContractRegistry()

# Solo los tipos con lógica real registran contrato; los stubs no son despachables.
task_contract_registry.register(TaskType.VECTORIZE_EVENT, VectorizeEventPayload)
task_contract_registry.register(TaskType.CONSOLIDATE_MEMORY, ConsolidateMemoryPayload)
