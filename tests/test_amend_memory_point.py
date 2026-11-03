"""
Tests del evento de corrección de metadata de memoria (ADR-009, #89).

Aísla la lógica del handler con Qdrant y sesión mockeados (patrón de la
suite del handler de imagen): cero interacción con la cola ni con la BD.

Valida:
- Localización auto-resuelta: el point_id se busca en las colecciones
  registradas (texto e imagen por igual — transversal, ADR-009).
- set_payload en Qdrant SIN tocar el vector (sin re-embedding) con la
  clave plana 'project'.
- Estrategia dual-store (decisión A, cerrada con evidencia del esquema):
  Qdrant primero; simetría relacional SOLO donde el registro relacional
  del punto almacena project (la task original de imagen — ADR-007-003:
  los tasks ya son eventos); en texto la traza relacional ES la propia
  task row del evento de corrección (Event no guarda project, FR-3 cero
  migración), con old_project y colección capturados (append-only).
- Punto inexistente (decisión B): MemoryPointNotFoundError — veneno
  determinista, jamás un no-op silencioso.
- Idempotencia por diseño: re-dispatch de la misma corrección converge.
- Extracción estricta: clave ausente = corrupción de cola (ADR-006).
"""

import uuid

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.db.models import Task, TaskStatus
from src.schemas.tasks import TaskType
from src.schemas.memory import DomainType
from src.workers.task_router import handle_amend_memory_point, MemoryPointNotFoundError
from src.repositories.vector import IMAGE_COLLECTION, REGISTERED_COLLECTIONS, VectorRepository

TASK_ID = uuid.UUID("b52c2d1e-9f34-4a71-8c60-1d2e3f4a5b6c")
SESSION_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
POINT_ID = uuid.UUID("7c60755c-02a3-4158-b8e4-6600b7650c17")


# =====================================================================
# HELPERS (Datos Sintéticos)
# =====================================================================

def _amend_task(**payload_overrides):
    """Tarea de corrección conforme al contrato: point_id + project."""
    payload = {
        "task_type": TaskType.AMEND_MEMORY_POINT.value,
        "point_id": str(POINT_ID),
        "project": "proyecto-corregido",
    }
    payload.update(payload_overrides)
    return Task(
        id=TASK_ID,
        session_id=SESSION_ID,
        status=TaskStatus.RUNNING,
        payload=payload,
    )


def _payload_result(payload):
    """Resultado síncrono de SQLAlchemy para el SELECT del JSONB payload."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = payload
    return result


def _session_mock(*execute_results):
    """Sesión async mockeada: un resultado por cada execute esperado.

    set_payload_fields consume dos executes por llamada (SELECT del JSONB
    + UPDATE atómico); el cortafuegos de rollback queda mockeado aparte.
    """
    session_mock = AsyncMock()
    session_mock.rollback = AsyncMock()
    session_mock.execute.side_effect = list(execute_results)
    return session_mock


def _qdrant_client_con_punto(collection_with_point, point_payload):
    """Cliente Qdrant mockeado: el punto solo existe en la colección dada.

    El retrieve responde [] en el resto — el handler tiene que ESCANEAR
    las colecciones registradas hasta localizarlo (auto-resolución).
    """
    client = AsyncMock()

    def _retrieve(collection_name, ids, with_payload, with_vectors):
        if collection_name == collection_with_point:
            point = MagicMock()
            point.payload = point_payload
            return [point]
        return []

    client.retrieve = AsyncMock(side_effect=_retrieve)
    client.set_payload = AsyncMock()
    return client


def _payload_escrito_en_update(execute_call):
    """Extrae el dict de payload que viajó en el UPDATE atómico del JSONB.

    La forma del repo es select→copy→update: el payload resultante es el
    único dict entre los params compilados del statement.
    """
    stmt = execute_call.args[0]
    params = stmt.compile().params
    for value in params.values():
        if isinstance(value, dict):
            return value
    return {}


# =====================================================================
# TESTS: TOPOLOGÍA REGISTRADA
# =====================================================================

class TestTopologiaRegistrada:
    """El universo donde vive todo punto despachable (ADR-009, #89)."""

    def test_colecciones_registradas_son_dominios_de_texto_mas_imagenes(self):
        """Una por dominio (BUG-001 #34) + la colección de imágenes (#50):
        la auto-resolución de la corrección nunca escanea de más ni de menos."""
        assert REGISTERED_COLLECTIONS == [d.value for d in DomainType] + [IMAGE_COLLECTION]
        assert IMAGE_COLLECTION in REGISTERED_COLLECTIONS


# =====================================================================
# TESTS: PUNTO DE TEXTO
# =====================================================================

class TestPuntoDeTexto:
    """Corrección de un punto de texto: Qdrant + traza relacional propia."""

    @pytest.mark.asyncio
    @patch("src.workers.task_router.get_qdrant_client", new_callable=AsyncMock)
    async def test_corrige_project_en_qdrant_y_deja_traza(self, mock_get_qdrant):
        """
        Point_id localizado en 'conversation' (auto-resolución escaneando):
        set_payload con la clave plana 'project', SIN tocar el vector. La
        traza relacional es la PROPIA task row del evento: colección y
        valor previo capturados (append-only — el Event origen queda
        intacto: no almacena project, FR-3 cero migración).
        """
        client = _qdrant_client_con_punto(
            "conversation",
            {"tenant_id": "99999999-9999-9999-9999-999999999999", "project": "proyecto-viejo"},
        )
        mock_get_qdrant.return_value = client

        session_mock = _session_mock(
            _payload_result({"task_type": "amend_memory_point", "point_id": str(POINT_ID), "project": "proyecto-corregido"}),
            MagicMock(),
        )

        await handle_amend_memory_point(_amend_task(), session_mock)

        # 1. Auto-resolución: escaneó las colecciones hasta 'conversation'
        escaneadas = [call.kwargs["collection_name"] for call in client.retrieve.call_args_list]
        assert escaneadas == ["document", "conversation"]

        # 2. set_payload en Qdrant: clave plana project, sin vector (wait=True)
        client.set_payload.assert_awaited_once()
        kwargs = client.set_payload.call_args.kwargs
        assert kwargs["collection_name"] == "conversation"
        assert kwargs["payload"] == {"project": "proyecto-corregido"}
        assert kwargs["points"] == [str(POINT_ID)]
        assert kwargs["wait"] is True

        # 3. Simetría relacional del TEXTO: solo la traza del propio evento
        #    (SELECT + UPDATE del JSONB de su task row) — cero espejo del original.
        assert session_mock.execute.await_count == 2

        # 4. Traza audible: old_project y colección capturados
        traza = _payload_escrito_en_update(session_mock.execute.call_args_list[1])
        assert traza["amended_collection"] == "conversation"
        assert traza["old_project"] == "proyecto-viejo"
        assert traza["task_type"] == "amend_memory_point"

        # 5. Transacción limpia: el commit pertenece al ciclo del worker
        session_mock.rollback.assert_not_awaited()


# =====================================================================
# TESTS: PUNTO DE IMAGEN
# =====================================================================

class TestPuntoDeImagen:
    """Corrección de un punto de imagen: Qdrant + espejo en la task original."""

    @pytest.mark.asyncio
    @patch("src.workers.task_router.get_qdrant_client", new_callable=AsyncMock)
    async def test_espejo_relacional_en_la_task_original(self, mock_get_qdrant):
        """
        El registro relacional del punto de imagen ES la task original
        (ADR-007-003) y su JSONB lleva project: la corrección actualiza
        Qdrant Y el espejo relacional, atómicamente por UUID. Orden
        registrado (decisión A): Qdrant primero, Postgres después.
        """
        client = _qdrant_client_con_punto(
            IMAGE_COLLECTION,
            {"tenant_id": "99999999-9999-9999-9999-999999999999", "project": "proyecto-viejo"},
        )
        mock_get_qdrant.return_value = client

        payload_original = {
            "task_type": "vectorize_image_event",
            "image": "data:image/png;base64,...",
            "project": "proyecto-viejo",
        }
        session_mock = _session_mock(
            _payload_result(payload_original),  # espejo: SELECT JSONB task original
            MagicMock(),                        # espejo: UPDATE atómico
            _payload_result({"task_type": "amend_memory_point", "point_id": str(POINT_ID), "project": "proyecto-corregido"}),
            MagicMock(),                        # traza: UPDATE atómico
        )

        await handle_amend_memory_point(_amend_task(), session_mock)

        # 1. set_payload sobre la colección de imágenes
        kwargs = client.set_payload.call_args.kwargs
        assert kwargs["collection_name"] == IMAGE_COLLECTION
        assert kwargs["payload"] == {"project": "proyecto-corregido"}

        # 2. Cuatro executes: espejo (SELECT+UPDATE) + traza (SELECT+UPDATE)
        assert session_mock.execute.await_count == 4

        # 3. Espejo relacional: el JSONB de la task original lleva el valor corregido
        espejo = _payload_escrito_en_update(session_mock.execute.call_args_list[1])
        assert espejo["project"] == "proyecto-corregido"
        assert espejo["task_type"] == "vectorize_image_event"  # el resto del evento, intacto

        # 4. Traza del propio evento de corrección igualmente audible
        traza = _payload_escrito_en_update(session_mock.execute.call_args_list[3])
        assert traza["amended_collection"] == IMAGE_COLLECTION
        assert traza["old_project"] == "proyecto-viejo"

        session_mock.rollback.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("src.workers.task_router.get_qdrant_client", new_callable=AsyncMock)
    async def test_espejo_relacional_ausente_es_fallo_explicito(self, mock_get_qdrant):
        """
        Punto vivo en Qdrant pero task original inexistente en Postgres:
        inconsistencia dual-store EXPLÍCITA (MemoryPointNotFoundError),
        jamás un COMPLETED silencioso con la simetría rota.
        """
        client = _qdrant_client_con_punto(IMAGE_COLLECTION, {"project": "proyecto-viejo"})
        mock_get_qdrant.return_value = client

        session_mock = _session_mock(
            _payload_result(None),  # la task original no existe (JSONB None)
        )

        with pytest.raises(MemoryPointNotFoundError) as exc_info:
            await handle_amend_memory_point(_amend_task(), session_mock)

        assert "inconsistencia dual-store" in str(exc_info.value).lower()
        # El set de Qdrant ya aplicó (orden registrado); el re-dispatch converge
        client.set_payload.assert_awaited_once()


# =====================================================================
# TESTS: FALLOS EXPLÍCITOS (decisión B del ADR-009)
# =====================================================================

class TestFallosExplicitos:
    """Punto inexistente = veneno determinista, jamás silencio."""

    @pytest.mark.asyncio
    @patch("src.workers.task_router.get_qdrant_client", new_callable=AsyncMock)
    async def test_punto_inexistente_falla_con_clase_propia(self, mock_get_qdrant):
        """
        point_id ausente de TODAS las colecciones registradas: la tarea
        muere FAILED con error_trace honesto (MemoryPointNotFoundError) —
        determinista para el futuro DLQ (#30), y ni set_payload ni la
        traza relacional se ejecutan.
        """
        client = _qdrant_client_con_punto(collection_with_point=None, point_payload={})
        mock_get_qdrant.return_value = client

        session_mock = _session_mock()

        with pytest.raises(MemoryPointNotFoundError) as exc_info:
            await handle_amend_memory_point(_amend_task(), session_mock)

        assert "no existe en ninguna colección" in str(exc_info.value)
        assert str(POINT_ID) in str(exc_info.value)
        client.set_payload.assert_not_awaited()
        session_mock.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_payload_sin_project_es_corrupcion_de_cola(self):
        """La frontera ya validó contra el contrato: una clave ausente solo
        puede ser corrupción de cola (ADR-006, extracción estricta)."""
        task = _amend_task()
        task.payload.pop("project")

        with pytest.raises(ValueError) as exc_info:
            await handle_amend_memory_point(task, _session_mock())

        assert "no cumple el contrato" in str(exc_info.value)
        assert "corrupción de cola" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_payload_no_dict_muere_antes_de_tocar_infra(self):
        task = _amend_task()
        task.payload = "payload corrupto: string"

        with pytest.raises(ValueError) as exc_info:
            await handle_amend_memory_point(task, _session_mock())

        assert "no es un objeto JSON" in str(exc_info.value)

    @pytest.mark.asyncio
    @patch("src.workers.task_router.get_qdrant_client", new_callable=AsyncMock)
    async def test_fallo_de_ejecucion_rola_back_y_relanza(self, mock_get_qdrant):
        """
        Fallo a mitad de la mutación dual-store (Qdrant cae tras localizar):
        cortafuegos — rollback de lo relacional pendiente y re-raise al
        fail_task del worker. El set de Qdrant NO se compensa: idempotente,
        el re-dispatch converge (estrategia registrada, decisión A).
        """
        client = _qdrant_client_con_punto("system", {"project": "proyecto-viejo"})
        client.set_payload.side_effect = RuntimeError("qdrant caído")
        mock_get_qdrant.return_value = client

        session_mock = _session_mock(
            _payload_result({"task_type": "amend_memory_point", "point_id": str(POINT_ID), "project": "proyecto-corregido"}),
            MagicMock(),
        )

        with pytest.raises(RuntimeError):
            await handle_amend_memory_point(_amend_task(), session_mock)

        session_mock.rollback.assert_awaited_once()


# =====================================================================
# TESTS: IDEMPOTENCIA POR DISEÑO
# =====================================================================

class TestIdempotencia:
    """Re-dispatch de la misma corrección converge al valor objetivo."""

    @pytest.mark.asyncio
    @patch("src.workers.task_router.get_qdrant_client", new_callable=AsyncMock)
    async def test_redispatch_de_la_misma_correccion_converge(self, mock_get_qdrant):
        """
        El segundo dispatch con el mismo valor objetivo es un no-op
        funcional: mismo set_payload (idempotente por construcción) y
        old_project ya igual al valor objetivo — converge sin duplicar
        nada ni romper la traza.
        """
        client = _qdrant_client_con_punto(
            "conversation", {"tenant_id": "t", "project": "proyecto-corregido"}
        )
        mock_get_qdrant.return_value = client

        for _ in range(2):
            session_mock = _session_mock(
                _payload_result({"task_type": "amend_memory_point", "point_id": str(POINT_ID), "project": "proyecto-corregido"}),
                MagicMock(),
            )
            await handle_amend_memory_point(_amend_task(), session_mock)

            traza = _payload_escrito_en_update(session_mock.execute.call_args_list[1])
            assert traza["old_project"] == "proyecto-corregido"  # ya convergido

        # Dos sets idénticos: el payload converge, no acumula
        assert client.set_payload.await_count == 2
        assert all(
            call.kwargs["payload"] == {"project": "proyecto-corregido"}
            for call in client.set_payload.call_args_list
        )


# =====================================================================
# TESTS: SEAM DE PERSISTENCIA (repositorio vectorial mudo)
# =====================================================================

class TestSeamRepositorioVectorial:
    """locate_point y amend_payload: primitivas mudas de la corrección."""

    @pytest.mark.asyncio
    async def test_locate_point_devuelve_coleccion_y_payload(self):
        client = AsyncMock()
        point = MagicMock()
        point.payload = {"project": "proyecto-viejo"}
        client.retrieve.side_effect = [[], [point]]  # document vacía, conversation lo tiene

        repo = VectorRepository(client=client)
        localizado = await repo.locate_point(str(POINT_ID))

        assert localizado == ("conversation", {"project": "proyecto-viejo"})
        assert client.retrieve.await_count == 2
        kwargs = client.retrieve.call_args.kwargs
        assert kwargs["ids"] == [str(POINT_ID)]
        assert kwargs["with_payload"] is True
        assert kwargs["with_vectors"] is False

    @pytest.mark.asyncio
    async def test_locate_point_ausente_devuelve_none(self):
        """Política del llamador: el repositorio reporta, no decide."""
        client = AsyncMock()
        client.retrieve.return_value = []

        repo = VectorRepository(client=client)
        assert await repo.locate_point(str(POINT_ID)) is None
        assert client.retrieve.await_count == len(REGISTERED_COLLECTIONS)

    @pytest.mark.asyncio
    async def test_amend_payload_set_payload_wait_true(self):
        client = AsyncMock()
        repo = VectorRepository(client=client)

        await repo.amend_payload(
            collection_name="conversation",
            point_id=str(POINT_ID),
            fields={"project": "proyecto-corregido"},
        )

        client.set_payload.assert_awaited_once()
        kwargs = client.set_payload.call_args.kwargs
        assert kwargs["collection_name"] == "conversation"
        assert kwargs["payload"] == {"project": "proyecto-corregido"}
        assert kwargs["points"] == [str(POINT_ID)]
        assert kwargs["wait"] is True
