"""
Tests de la provisión declarativa de entidades (ADR-012-001, #112).

El fichero YAML del despliegue declara las identidades del inquilino y el
bootstrap las garantiza con el patrón ensure_* del ADR-011 aplicado a datos:
INSERT ... ON CONFLICT (slug) DO NOTHING, system_default implícito y reclamo
dirigido (aprobado por David, 25 nov) de la fila system_default histórica
auto-vivificada. La tabla jamás se recorta: quitar una entidad del YAML no
la borra (regla aditiva aplicada a filas del despliegue).

- Unitarios (sin BD): validación del loader (charset, duplicados,
  estructura, malformados) y SQL emitido por ensure_entities contra una
  conexión mockeada.
- Integración (coreai_test_db, cortafuegos del conftest): idempotencia
  exacta sobre BD limpia y con historia, aditividad, fichero ausente o
  vacío con solo system_default y error determinista si es inválido.

DoD del issue #112, caso a caso:
- Idempotencia (N arranques = tabla idéntica, BD limpia y con historia):
  tests de integración de abajo.
- Aditividad (nada se modifica ni se borra): reclamo circunscrito +
  entidad desaparecida del YAML sigue presente.
- Sin YAML / YAML vacío -> solo system_default.
- YAML malformado -> error de arranque claro y determinista (BD intacta:
  la validación corre antes de cualquier DDL o INSERT).
- Charset del id validado al leer el YAML (unitarios del loader).
"""
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

from src.db.bootstrap import ensure_entities, ensure_relational_schema
from src.db.entities_config import (
    DECLARED_ROLE,
    SYSTEM_DEFAULT_ROLE,
    SYSTEM_DEFAULT_SLUG,
    EntitiesProvisionError,
    load_entity_declarations,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _apuntar_entities_file(monkeypatch, tmp_path, contenido: str | None) -> str:
    """Hermetic: ENTITIES_FILE siempre explícito en el test (jamás ambient)."""
    if contenido is None:
        path = tmp_path / "entities_ausente.yaml"  # no se crea: fichero ausente
    else:
        path = tmp_path / "entities.yaml"
        path.write_text(contenido, encoding="utf-8")
    monkeypatch.setenv("ENTITIES_FILE", str(path))
    return str(path)


def _mock_conn(order: list) -> MagicMock:
    """Conn mockeada que registra el SQL del bootstrap de entidades."""
    conn = MagicMock()
    conn.dialect = postgresql.dialect()

    async def _execute(stmt, params=None, **kw):
        sql = str(stmt.compile(dialect=conn.dialect)) if hasattr(stmt, "compile") else str(stmt)
        order.append(sql)
        return []

    conn.execute = AsyncMock(side_effect=_execute)
    return conn


# ---------------------------------------------------------------------------
# Unitarios del loader (validación del YAML, fichero y razón en el error)
# ---------------------------------------------------------------------------


def test_loader_fichero_ausente_devuelve_lista_vacia(tmp_path):
    assert load_entity_declarations(str(tmp_path / "no_existe.yaml")) == []


def test_loader_fichero_vacio_devuelve_lista_vacia(tmp_path):
    path = tmp_path / "entities.yaml"
    path.write_text("", encoding="utf-8")
    assert load_entity_declarations(str(path)) == []


def test_loader_yaml_valido_preserva_orden_y_labels(tmp_path):
    path = tmp_path / "entities.yaml"
    path.write_text(
        "entities:\n  - id: david\n    label: David Gómez\n  - id: elios-workshop\n",
        encoding="utf-8",
    )
    decls = load_entity_declarations(str(path))
    assert [(d.id, d.label) for d in decls] == [("david", "David Gómez"), ("elios-workshop", None)]


def test_loader_raiz_lista_o_escalar_es_malformado(tmp_path):
    path = tmp_path / "entities.yaml"
    path.write_text("- id: david\n", encoding="utf-8")
    with pytest.raises(EntitiesProvisionError, match="mapping"):
        load_entity_declarations(str(path))
    path.write_text("42\n", encoding="utf-8")
    with pytest.raises(EntitiesProvisionError, match="mapping"):
        load_entity_declarations(str(path))


def test_loader_clave_desconocida_en_la_raiz_es_malformado(tmp_path):
    path = tmp_path / "entities.yaml"
    path.write_text("entities: []\nroles: []\n", encoding="utf-8")
    with pytest.raises(EntitiesProvisionError, match="claves desconocidas"):
        load_entity_declarations(str(path))


def test_loader_entities_no_lista_es_malformado(tmp_path):
    path = tmp_path / "entities.yaml"
    path.write_text("entities:\n  david: {}\n", encoding="utf-8")
    with pytest.raises(EntitiesProvisionError, match="lista"):
        load_entity_declarations(str(path))


def test_loader_direccion_en_vez_de_fichero_es_malformado(tmp_path):
    # La ruta apunta a un directorio: ilegible = determinista, jamás silencio.
    with pytest.raises(EntitiesProvisionError, match="ilegible"):
        load_entity_declarations(str(tmp_path))


def test_loader_charset_del_id_validado(tmp_path):
    path = tmp_path / "entities.yaml"
    for malformed in [
        "entities:\n  - id: David\n",       # mayúsculas
        "entities:\n  - id: 'david gomez'\n",  # espacio
        "entities:\n  - id: david@correo\n",  # charset prohibido
        "entities:\n  - id: ''\n",          # vacío
        "entities:\n  - label: sin-id\n",   # id ausente
        "entities:\n  - id: 42\n",          # no string
        "entities:\n  - {}\n",             # entrada vacía
        "entities:\n  - 'david'\n",         # entrada no mapping
    ]:
        path.write_text(malformed, encoding="utf-8")
        with pytest.raises(EntitiesProvisionError, match=str(path)):
            load_entity_declarations(str(path))


def test_loader_id_y_label_demasiado_largos_son_malformados(tmp_path):
    path = tmp_path / "entities.yaml"
    path.write_text(f"entities:\n  - id: {'d' * 65}\n", encoding="utf-8")
    with pytest.raises(EntitiesProvisionError, match="excede 64"):
        load_entity_declarations(str(path))
    path.write_text(
        f"entities:\n  - id: david\n    label: {'l' * 256}\n", encoding="utf-8"
    )
    with pytest.raises(EntitiesProvisionError, match="excede 255"):
        load_entity_declarations(str(path))


def test_loader_clave_desconocida_en_entrada_es_malformado(tmp_path):
    path = tmp_path / "entities.yaml"
    path.write_text("entities:\n  - id: david\n    rol: user\n", encoding="utf-8")
    with pytest.raises(EntitiesProvisionError, match="claves desconocidas"):
        load_entity_declarations(str(path))


def test_loader_label_no_string_es_malformado(tmp_path):
    path = tmp_path / "entities.yaml"
    path.write_text("entities:\n  - id: david\n    label: 42\n", encoding="utf-8")
    with pytest.raises(EntitiesProvisionError, match="label debe ser un string"):
        load_entity_declarations(str(path))


def test_loader_ids_duplicados_son_malformados(tmp_path):
    path = tmp_path / "entities.yaml"
    path.write_text("entities:\n  - id: david\n  - id: david\n", encoding="utf-8")
    with pytest.raises(EntitiesProvisionError, match="duplicado"):
        load_entity_declarations(str(path))


def test_loader_yaml_sintacticamente_roto_es_malformado(tmp_path):
    path = tmp_path / "entities.yaml"
    path.write_text("entities: [ {id: david\n", encoding="utf-8")
    with pytest.raises(EntitiesProvisionError, match="YAML inválido"):
        load_entity_declarations(str(path))


def test_loader_el_error_lleva_fichero_y_razon(tmp_path):
    path = tmp_path / "entities.yaml"
    path.write_text("entities:\n  - id: David\n", encoding="utf-8")
    with pytest.raises(EntitiesProvisionError) as exc_info:
        load_entity_declarations(str(path))
    assert str(path) in str(exc_info.value)
    assert "charset" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Unitarios de ensure_entities (SQL emitido contra conn mockeada)
# ---------------------------------------------------------------------------


async def test_ensure_entities_clama_la_fila_legacy_e_inserta_en_orden():
    from src.db.entities_config import EntityDeclaration

    order: list = []
    conn = _mock_conn(order)

    declarations = [
        EntityDeclaration(id="david", label="David Gómez"),
        EntityDeclaration(id="elios-workshop", label=None),
    ]
    await ensure_entities(conn, declarations)

    # Reclamo primero (la fila legacy de system_default existe antes que nada
    # más), luego system_default implícito, luego las declaradas en orden.
    assert order[0].startswith("UPDATE entities")
    # Los binds JAMÁS aparecen como :param en el SQL compilado (el dialecto
    # los reescribe a pyformat/format): se verifica como bound params.
    first_params = conn.execute.await_args_list[0].args[1]
    assert first_params == {"slug": SYSTEM_DEFAULT_SLUG, "role": SYSTEM_DEFAULT_ROLE}
    assert order[1].startswith("INSERT INTO entities")
    assert "ON CONFLICT (slug) DO NOTHING" in order[1]
    assert order[2].startswith("INSERT INTO entities")
    assert order[3].startswith("INSERT INTO entities")
    assert len(order) == 4

    # El reclamo está circunscrito: solo filas role='system_default' sin slug
    # y solo si ninguna ha reclamado aún.
    assert "slug IS NULL" in order[0]
    assert "NOT EXISTS" in order[0]


async def test_ensure_entities_roles_correctos_por_tipo_de_entidad():
    from src.db.entities_config import EntityDeclaration

    conn = _mock_conn([])
    declarations = [EntityDeclaration(id="david", label=None)]
    await ensure_entities(conn, declarations)

    stmts = [c.args[0] for c in conn.execute.await_args_list]
    system_default_stmt = stmts[1]
    declared_stmt = stmts[2]
    compiled_params = [
        system_default_stmt.compile(dialect=postgresql.dialect()).params,
        declared_stmt.compile(dialect=postgresql.dialect()).params,
    ]
    assert compiled_params[0]["slug"] == SYSTEM_DEFAULT_SLUG
    assert compiled_params[0]["role"] == SYSTEM_DEFAULT_ROLE
    assert compiled_params[1]["slug"] == "david"
    assert compiled_params[1]["role"] == DECLARED_ROLE


async def test_ensure_entities_sin_declaraciones_inserta_solo_system_default():
    conn = _mock_conn([])
    await ensure_entities(conn, [])
    stmts = [c.args[0] for c in conn.execute.await_args_list]
    assert len(stmts) == 2  # reclamo + system_default implícito
    for stmt in stmts[1:]:
        compiled = str(stmt.compile(dialect=postgresql.dialect()))
        assert "ON CONFLICT (slug) DO NOTHING" in compiled


# ---------------------------------------------------------------------------
# Integración (coreai_test_db — el cortafuegos del conftest fija la URL)
# ---------------------------------------------------------------------------


async def _limpiar_entidades():
    from src.db.database import engine

    async with engine.begin() as conn:
        # ondelete CASCADE en cadena: sessions -> tasks/messages, events,
        # llm_audit queda en entity_id NULL.
        await conn.execute(text("DELETE FROM entities"))


async def _slugs_presentes() -> dict[str, tuple]:
    from src.db.database import engine

    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text("SELECT slug, role, label, id FROM entities WHERE slug IS NOT NULL")
            )
        ).all()
    return {slug: (role, label, entity_id) for slug, role, label, entity_id in rows}


async def test_integracion_bd_limpia_con_dos_declaradas_y_idempotencia_exacta(
    monkeypatch, tmp_path
):
    """DoD: N arranques dejan la tabla idéntica (BD limpia)."""
    yaml_content = (
        "entities:\n"
        "  - id: david\n    label: David Gómez\n"
        "  - id: elios-workshop\n"
    )
    _apuntar_entities_file(monkeypatch, tmp_path, yaml_content)

    await _limpiar_entidades()
    await ensure_relational_schema()

    primera = await _slugs_presentes()
    assert set(primera) == {"system_default", "david", "elios-workshop"}
    assert primera["system_default"][0] == SYSTEM_DEFAULT_ROLE
    assert primera["david"] == (DECLARED_ROLE, "David Gómez", primera["david"][2])
    assert primera["elios-workshop"][0] == DECLARED_ROLE

    # Idempotencia exacta: mismos UUIDs tras re-garantizar (dos veces más).
    await ensure_relational_schema()
    await ensure_relational_schema()
    segunda = await _slugs_presentes()
    for slug in primera:
        assert primera[slug][2] == segunda[slug][2]
    assert set(primera) == set(segunda)


async def test_integracion_fichero_ausente_arranque_solo_system_default(monkeypatch, tmp_path):
    _apuntar_entities_file(monkeypatch, tmp_path, None)

    await _limpiar_entidades()
    await ensure_relational_schema()

    presentes = await _slugs_presentes()
    assert set(presentes) == {SYSTEM_DEFAULT_SLUG}


async def test_integracion_fichero_vacio_arranque_solo_system_default(monkeypatch, tmp_path):
    _apuntar_entities_file(monkeypatch, tmp_path, "entities: []\n")

    await _limpiar_entidades()
    await ensure_relational_schema()

    presentes = await _slugs_presentes()
    assert set(presentes) == {SYSTEM_DEFAULT_SLUG}


async def test_integracion_malformado_error_determinista_y_bd_intacta(monkeypatch, tmp_path):
    path = _apuntar_entities_file(monkeypatch, tmp_path, "entities: [ {id: David\n")

    await _limpiar_entidades()
    # La validación corre ANTES de cualquier DDL/INSERT: la mesa queda puesta
    # solo si algún arranque previo ya garantizó el esquema; aquí no importa:
    # un arranque fallido no deja cambios a medias.
    with pytest.raises(EntitiesProvisionError) as exc_info:
        await ensure_relational_schema()
    assert path in str(exc_info.value)

    presentes = await _slugs_presentes()
    assert presentes == {}  # BD intacta: cero INSERT a medias


async def test_integracion_reclama_la_fila_legacy_y_no_toca_a_los_demas(monkeypatch, tmp_path):
    """DoD: sobre BD con historia, la fila system_default auto-vivificada queda
    reclutada (mismo UUID, mismo role) y cualquier otra fila preexistente ni se
    toca ni se borra (aditividad explícita)."""
    from src.db.database import AsyncSessionLocal
    from src.repositories.relational import EntityRepository

    _apuntar_entities_file(monkeypatch, tmp_path, None)

    await _limpiar_entidades()
    await ensure_relational_schema()  # esquema garantizado (fila legacy simulada aparte)

    async with AsyncSessionLocal() as session:
        legacy_default = await EntityRepository(session).create(role="system_default")
        otra = await EntityRepository(session).create(role="test_historia")
        await session.commit()
        legacy_id = legacy_default.id
        otra_id = otra.id

    await ensure_relational_schema()

    presentes = await _slugs_presentes()
    assert set(presentes) == {SYSTEM_DEFAULT_SLUG}
    assert presentes[SYSTEM_DEFAULT_SLUG][2] == legacy_id  # MISMO UUID: reclamo, no fila nueva

    async with AsyncSessionLocal() as session:
        from src.db.models import Entity

        entidad_ajena = await session.get(Entity, otra_id)
        assert entidad_ajena is not None
        assert entidad_ajena.slug is None  # intacta: el reclamo solo toca system_default
        assert entidad_ajena.role == "test_historia"

    # Idempotencia del reclamo: un segundo arranque no crea ni duplica nada.
    await ensure_relational_schema()
    presentes2 = await _slugs_presentes()
    assert presentes2[SYSTEM_DEFAULT_SLUG][2] == legacy_id
    assert len(presentes2) == 1


async def test_integracion_aditividad_quitar_entidad_del_yaml_no_la_borra(monkeypatch, tmp_path):
    """DoD: nada UPDATEa ni borra filas declaradas preexistentes; quitar una
    entidad del YAML la deja viva (solo deja de garantizarse)."""
    _apuntar_entities_file(monkeypatch, tmp_path, "entities:\n  - id: david\n")

    await _limpiar_entidades()
    await ensure_relational_schema()

    david_id = (await _slugs_presentes())["david"][2]

    # Nueva realidad del despliegue: el YAML ya no declara a david.
    _apuntar_entities_file(monkeypatch, tmp_path, "entities: []\n")
    await ensure_relational_schema()

    presentes = await _slugs_presentes()
    assert "david" in presentes  # sigue viva: la provisión jamás borra
    assert presentes["david"][2] == david_id  # ni se toca: mismo UUID


async def test_integracion_dos_arranques_no_duplican_jamas(monkeypatch, tmp_path):
    """El índice unique de slug + ON CONFLICT DO NOTHING hacen imposible la
    duplicación: dos arranques declarando lo mismo dejan exactamente las
    mismas filas. Idempotencia puntual del INSERT, DoD del issue."""
    _apuntar_entities_file(monkeypatch, tmp_path, "entities:\n  - id: david\n")

    await _limpiar_entidades()
    await ensure_relational_schema()
    await ensure_relational_schema()

    from src.db.database import engine

    async with engine.connect() as conn:
        total = (
            await conn.execute(text("SELECT COUNT(*) FROM entities"))
        ).scalar()
    assert total == 2  # system_default + david, ni uno más
