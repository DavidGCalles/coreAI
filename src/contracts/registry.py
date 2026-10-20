"""
Registro Central de Contratos de Tarea (Task Contract Registry) — ADR-006-001 (#43).

Única fuente de verdad de los contratos de payload por TaskType (ADR-006,
`docs/adr/006-mcp-contracts.md`). Sirve las superficies del contrato desde el
mismo objeto:

    validate(task_type, payload) -> dict      validación/normalización (loader del contrato)
    schema(task_type)           -> dict       JSON Schema servido como MCP resource
    list()                      -> list[str]  catálogo de tipos contratados
    contract_uri(task_type)     -> str        URI del resource del contrato (ADR-006-002)

Regla estructural: NINGÚN tipo es despachable sin contrato registrado. La
frontera del dispatch (`coreai_dispatch_task`) rechaza explícitamente los tipos
sin contrato (error isError, no dict libre). La validación de la FORMA del
payload dentro de la tool call es ADR-006-003 (#44): la frontera llama a
`validate()`, que delega en el `load()` de la propia clase del contrato — la
normalización NO vive perdida en la frontera ni enterrada en el registro: vive
en el schema, junto a los defaults que materializa.

La misma definición que valida en runtime es la que se sirve como schema:
cero duplicación entre lo que el cliente descubre (ADR-006-002) y lo que la
frontera aplica (ADR-006-003).

`contract_uri()` es la única fuente del patrón de URI del resource. Hasta que
ADR-006-002 (#42) sirva el catálogo como resources, la URI viaja como
placeholder informativo en el error inline de la frontera (decisión David,
Oct 20): señala dónde vivirá el contrato, y el error incluye la esquema
compacta para que un agente se autocorriga sin leer código fuente.

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

CONTRACT_URI_TEMPLATE = "coreai://contracts/tasks/{task_type}"


def contract_uri(task_type: "TaskType | str") -> str:
    """
    URI del resource del contrato (ADR-006-002).

    Única fuente del patrón: la frontera la cita en sus errores (ADR-006-003) y
    #42 la servirá como resource. Placeholder informativo hasta entonces.
    """
    value = task_type.value if isinstance(task_type, TaskType) else str(task_type)
    return CONTRACT_URI_TEMPLATE.format(task_type=value)


class ContractNotRegisteredError(Exception):
    """Señal explícita: el TaskType solicitado no tiene contrato en el registro."""


class TaskPayloadContract(BaseModel):
    """
    Base de los contratos de payload de tarea (ADR-006).

    El loader vive AQUÍ, junto al schema que valida — no perdido en la frontera
    del dispatch ni escondido en el registro. `load()` es la única puerta
    canónica de entrada de un payload crudo: valida la forma y devuelve el dict
    NORMALIZADO (defaults materializados) listo para persistir. El dispatch
    (ADR-006-003) mete al motor únicamente payloads salidos de este loader.
    """

    model_config = ConfigDict(extra="forbid")

    @classmethod
    def load(cls, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Loader del contrato: valida y normaliza un payload crudo.

        Returns:
            dict[str, Any]: el payload canónico con los defaults materializados.

        Raises:
            pydantic.ValidationError: el payload viola el contrato.
        """
        return cls.model_validate(payload).model_dump(mode="json")


class VectorizeEventPayload(TaskPayloadContract):
    """Contrato formal de `vectorize_event` — semántica actual del handler (Issue 4.3)."""

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
    """Contrato formal de `consolidate_memory` — en línea con su handler."""

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
        self._contracts: dict[TaskType, type[TaskPayloadContract]] = {}

    def register(self, task_type: TaskType, contract: type[TaskPayloadContract]) -> None:
        """Da de alta el contrato de un TaskType. Reemplaza si ya existía (idempotente)."""
        self._contracts[task_type] = contract

    def has_contract(self, task_type: TaskType) -> bool:
        """Indica si el tipo es despachable (tiene contrato registrado)."""
        return task_type in self._contracts

    def validate(self, task_type: TaskType, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Valida y normaliza el payload contra el contrato del tipo — delega en el
        `load()` de la clase del contrato (la normalización vive en el schema).

        Raises:
            ContractNotRegisteredError: el tipo no tiene contrato
                (sin contrato no hay despacho).
            pydantic.ValidationError: el payload viola el contrato del tipo.
        """
        return self._require(task_type).load(payload)

    def schema(self, task_type: TaskType) -> dict[str, Any]:
        """JSON Schema del contrato — la misma definición que valida en runtime."""
        return self._require(task_type).model_json_schema()

    def list(self) -> list[str]:
        """Catálogo de tipos contratados (despachables)."""
        return sorted(task_type.value for task_type in self._contracts)

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

# Altas del tracer: solo los dos tipos con lógica real tienen contrato.
# Los stubs (dummy_test_task, extract_entities, init_session, follow_up) NO
# registran contrato: sin contrato no hay despacho — no son superficies reales.
task_contract_registry.register(TaskType.VECTORIZE_EVENT, VectorizeEventPayload)
task_contract_registry.register(TaskType.CONSOLIDATE_MEMORY, ConsolidateMemoryPayload)
