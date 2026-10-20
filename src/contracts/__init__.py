"""
Paquete de contratos de tarea (ADR-006).

Registro central de contratos: la única fuente de verdad de la forma del
payload por TaskType. Ver ADR-006 (docs/adr/006-mcp-contracts.md).
"""

from src.contracts.registry import (
    CONTRACT_URI_TEMPLATE,
    ConsolidateMemoryPayload,
    ContractNotRegisteredError,
    TaskContractRegistry,
    TaskPayloadContract,
    VectorizeEventPayload,
    contract_uri,
    task_contract_registry,
)

__all__ = [
    "CONTRACT_URI_TEMPLATE",
    "ConsolidateMemoryPayload",
    "ContractNotRegisteredError",
    "TaskContractRegistry",
    "TaskPayloadContract",
    "VectorizeEventPayload",
    "contract_uri",
    "task_contract_registry",
]
