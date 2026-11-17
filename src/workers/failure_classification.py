"""
Clasificación de fallos de ejecución de tareas (4.4-002 #104).

La frontera de la resiliencia del worker: un fallo DETERMINISTA reproduce
el mismo error en cada reintento (payload que viola el contrato, tipo
fuera de catálogo, point_id inexistente, brecha de implementación) —
reintentarlo solo quema cola y devuelve lo mismo, así que la tarea muere
en FAILED a la primera (la DLQ lógica del 4.4: FAILED + error_history).
El resto de fallos (red, timeouts, 5xx del gateway) es TRANSITORIO: el
worker lo difiere con backoff por si la infra se recupera (retry_task).

Terminología [DAVID, 17 nov]: "fallo determinista" en toda la superficie
(nombres, logs, comentarios) en vez del coloquial "veneno" (poison
message). NotImplementedError entra como determinista: nadie va a
implementar la lógica "por amor al arte después de un reintento".
"""

from pydantic import ValidationError

from src.contracts.registry import ContractNotRegisteredError
from src.workers.task_router import MemoryPointNotFoundError

# Fallo determinista = no reintentable. Nota: en pydantic v2 ValidationError
# ya hereda de ValueError — se lista explícito por documentar la frontera de
# contrato (ADR-006), aunque isinstance ya lo cubriría.
FALLOS_DETERMINISTAS: tuple[type[Exception], ...] = (
    # Catálogo y corrupción de cola (route_task y handlers): tipo fuera de
    # catálogo, payload sin 'content', sesión inexistente, enums inválidos.
    ValueError,
    # Brecha de implementación (route_task): tipo CONTRATADO sin handler en
    # TASK_REGISTRY, o tipo del esquema SIN contrato (corrupción de cola).
    NotImplementedError,
    # Frontera ADR-006: el tipo no tiene contrato registrado — sin contrato
    # no hay despacho.
    ContractNotRegisteredError,
    # Frontera ADR-006: el payload viola el contrato del tipo.
    ValidationError,
    # ADR-009 (#89): point_id inexistente — la corrección re-orienta lo
    # almacenado, no fabrica puntos.
    MemoryPointNotFoundError,
)


def es_fallo_determinista(error: Exception) -> bool:
    """
    ¿Este error se reproduciría idéntico en cada reintento?

    True → fail_task: FAILED inmediato, cero reintentos, la tarea queda en
    la DLQ lógica. False → retry_task: fallo transitorio, la tarea vuelve a
    PENDING diferida con backoff.
    """
    return isinstance(error, FALLOS_DETERMINISTAS)
