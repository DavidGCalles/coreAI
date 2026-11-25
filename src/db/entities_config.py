"""
Provisión declarativa de entidades (ADR-012-001, #112).

Carga y validación del fichero YAML de entidades del despliegue: las
identidades del inquilino viven ahí, montadas desde el volumen de datos,
y JAMÁS viajan por variables de entorno (D2 del ADR-012: cualquier
aplicación consumidora podría leerse el env completo del stack).

Semántica de fallo (regla de la frontera, ADR-012):
- Fichero ausente o vacío -> lista vacía: el arranque queda en solo
  `system_default` (garantizado aparte por el bootstrap, siempre).
- Fichero malformado (sintaxis, estructura, charset, duplicados) ->
  EntitiesProvisionError: fallo DETERMINISTA con fichero y razón. Nada
  de arranque a medias ni silencio, y nada de backoff (reintentar el
  mismo fichero daría el mismo error).

El loader es puro a propósito (no toca BD ni engine): la suite ejercita
toda la validación sin infraestructura.
"""
import logging
import re
from dataclasses import dataclass

import yaml

logger = logging.getLogger(__name__)

# Identidad implícita: el bootstrap la garantiza SIEMPRE, la declare o no
# el YAML. mismo slug y role de la fila que hoy auto-vivifica la frontera.
SYSTEM_DEFAULT_SLUG = "system_default"
SYSTEM_DEFAULT_ROLE = "system_default"
# Metadata que hoy lleva la fila auto-vivificada de system_default: paridad
# exacta para que la fila garantizada por el bootstrap sea indistinguible.
SYSTEM_DEFAULT_METADATA = {"description": "Fallback entity for orphaned tasks"}
# Role de toda entidad declarada (la taxonomía/RBAC posterior es otro ADR).
DECLARED_ROLE = "user"

# Charset del id declarativo, simétrico al document_id del ADR-010.
ID_PATTERN = re.compile(r"^[a-z0-9_-]+$")
MAX_ID_LENGTH = 64
MAX_LABEL_LENGTH = 255


class EntitiesProvisionError(RuntimeError):
    """Fallo determinista de provisión: el fichero es inválido tal cual."""


def _fail(path: str, reason: str) -> EntitiesProvisionError:
    return EntitiesProvisionError(f"{path}: {reason}")


@dataclass(frozen=True)
class EntityDeclaration:
    """Entrada validada del YAML: id (charset restringido) + label opcional."""
    id: str
    label: str | None


def load_entity_declarations(path: str) -> list[EntityDeclaration]:
    """
    Lee y valida el fichero YAML de entidades. Puro: solo fichero y razón
    en el error, jamás un arranque a medias.

    - Ausente o vacío -> [] (arranque autónomo: solo system_default).
    - Cualquier violación -> EntitiesProvisionError determinista.
    """
    try:
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except FileNotFoundError:
        return []
    except (OSError, yaml.YAMLError) as exc:
        raise _fail(path, f"fichero ilegible o YAML inválido — {type(exc).__name__}: {exc}") from exc

    if raw is None:
        return []
    if not isinstance(raw, dict):
        raise _fail(path, "la raíz debe ser un mapping con la clave 'entities'")
    unknown_root_keys = set(raw) - {"entities"}
    if unknown_root_keys:
        raise _fail(path, f"claves desconocidas en la raíz: {sorted(unknown_root_keys)}")

    entries = raw.get("entities")
    if entries is None:
        return []
    if not isinstance(entries, list):
        raise _fail(path, "'entities' debe ser una lista de mappings con 'id' (y 'label' opcional)")

    declarations: list[EntityDeclaration] = []
    seen: set[str] = set()
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise _fail(path, f"entities[{i}] debe ser un mapping con 'id' (y 'label' opcional)")
        unknown_keys = set(entry) - {"id", "label"}
        if unknown_keys:
            raise _fail(path, f"entities[{i}] tiene claves desconocidas: {sorted(unknown_keys)}")

        entity_id = entry.get("id")
        if not isinstance(entity_id, str) or not ID_PATTERN.match(entity_id):
            raise _fail(
                path,
                f"entities[{i}].id debe ser un string con charset [{ID_PATTERN.pattern}]",
            )
        if len(entity_id) > MAX_ID_LENGTH:
            raise _fail(path, f"entities[{i}].id excede {MAX_ID_LENGTH} caracteres")

        label = entry.get("label")
        if label is not None:
            if not isinstance(label, str):
                raise _fail(path, f"entities[{i}].label debe ser un string")
            if len(label) > MAX_LABEL_LENGTH:
                raise _fail(path, f"entities[{i}].label excede {MAX_LABEL_LENGTH} caracteres")

        if entity_id in seen:
            raise _fail(path, f"id duplicado: '{entity_id}'")
        seen.add(entity_id)

        declarations.append(EntityDeclaration(id=entity_id, label=label))

    return declarations
