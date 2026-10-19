"""
Registro Central de Contratos de Tarea (Task Contract Registry) — ADR-006-001 (#43).

Única fuente de verdad de los contratos de payload por TaskType (ADR-006,
`docs/adr/006-mcp-contracts.md`). Sirve tres superficies desde el mismo objeto:

    validate(task_type, payload) -> dict      validación/normalización del payload
    schema(task_type)           -> dict       JSON Schema servido como MCP resource
    list()                      -> list[str]  catálogo de tipos contratados

Regla estructural: NINGÚN tipo es despachable sin contrato registrado. La
frontera del dispatch (`coreai_dispatch_task`) rechaza explícitamente los tipos
sin contrato (error isError, no dict libre). La validación de la FORMA del
payload dentro de la tool call llega con ADR-006-003 (#44); este registro ya
expone `validate()` para servir a esa frontera desde el mismo objeto que
expondrá los resources de ADR-006-002 (#42) — cero duplicación entre lo que el
cliente lee y lo que el servidor aplica.

Costura con el futuro registro de catálogos abiertos (interfaz documentada, sin
decidir apertura): el registro de catálogos abiertos NO reemplaza a este módulo,
lo alimenta. El alta de un nuevo TaskType en aquel registro será la señal para
`register()` aquí — un tipo sin contrato sigue sin siendo despachable. El
catálogo cerrado actual (`src.schemas.tasks.TaskType`) es por ahora el único
proveedor de claves de este registro.
"""

from pydantic import BaseModel, ConfigDict, Field
from typing import Any

from src.schemas.tasks import TaskType
from src.schemas.memory import DomainType, Visibility


class ContractNotRegisteredError(Exception):
    """Señal explícita: el TaskType solicitado no tiene contrato en el registro."""


class VectorizeEventPayload(BaseModel):
    """Contrato formal de `vectorize_event` — semántica actual del handler (Issue 4.3)."""
    model_config = ConfigDict(extra="forbid")

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


class ConsolidateMemoryPayload(BaseModel):
    """Contrato formal de `consolidate_memory` — en línea con su handler."""
    model_config = ConfigDict(extra="forbid")

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
    """
    Fuente única de verdad de los contratos de tarea.

    La misma definición que valida en runtime es la que se sirve como schema:
    cero duplicación entre lo que el cliente descubre (ADR-006-002) y lo que la
    frontera aplica (ADR-006-003).
    """

    def __init__(self) -> None:
        self._contracts: dict[TaskType, type[BaseModel]] = {}

    def register(self, task_type: TaskType, contract: type[BaseModel]) -> None:
        """Da de alta el contrato de un TaskType. Reemplaza si ya existía (idempotente)."""
        self._contracts[task_type] = contract

    def has_contract(self, task_type: TaskType) -> bool:
        """Indica si el tipo es despachable (tiene contrato registrado)."""
        return task_type in self._contracts

    def validate(self, task_type: TaskType, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Valida y normaliza el payload contra el contrato del tipo.

        Raises:
            ContractNotRegisteredError: el tipo no tiene contrato
                (sin contrato no hay despacho).
            pydantic.ValidationError: el payload viola el contrato del tipo.
        """
        contract = self._require(task_type)
        return contract.model_validate(payload).model_dump(mode="json")

    def schema(self, task_type: TaskType) -> dict[str, Any]:
        """JSON Schema del contrato — la misma definición que valida en runtime."""
        return self._require(task_type).model_json_schema()

    def list(self) -> list[str]:
        """Catálogo de tipos contratados (despachables)."""
        return sorted(task_type.value for task_type in self._contracts)

    def _require(self, task_type: TaskType) -> type[BaseModel]:
        contract = self._contracts.get(task_type)
        if contract is None:
            raise ContractNotRegisteredError(
                f"El tipo de tarea '{task_type}' no tiene contrato registrado. "
                f"Sin contrato no hay despacho (ADR-006-001). "
                f"Tipos contratados: {', '.join(self.list()) or 'ninguno'}."
            )
        return contract


task_contract_registry = TaskContractRegistry()

# Altas del tracer: solo los dos tipos con lógica real tienen contrato.
# Los stubs (dummy_test_task, extract_entities, init_session, follow_up) NO
# registran contrato: sin contrato no hay despacho — no son superficies reales.
task_contract_registry.register(TaskType.VECTORIZE_EVENT, VectorizeEventPayload)
task_contract_registry.register(TaskType.CONSOLIDATE_MEMORY, ConsolidateMemoryPayload)
