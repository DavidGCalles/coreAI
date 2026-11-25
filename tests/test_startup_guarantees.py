"""
Suite hermética de las garantías de arranque del BUG-011 (#107).

Sin Qdrant real ni Postgres real: fakes que cuentan llamadas y fallan las
veces que hace falta. Aquí se defiende el patrón (backoff, idempotencia y
cierre del singleton dentro del loop efímero); el E2E de despliegue —
worker solo contra una BD limpia, core-mcp solo contra un Qdrant limpio —
es validación en la VM.
"""

import asyncio
import types

import pytest

import src.main as main_module
import src.worker as worker_module
from src.db import vector_db
from src.db.vector_db import close_qdrant_client, ensure_collections
from src.repositories.vector import (
    IMAGE_COLLECTION,
    TEXT_VECTOR_SIZE,
    TEXT_VECTOR_DISTANCE,
    UnexpectedResponse,
    VectorRepository,
)
from src.schemas.memory import DomainType


class FakeQdrantClient:
    """Cliente que registra su topología y sabe cerrarse."""

    def __init__(self):
        self.vectors_config = {}
        self.cerrado = False

    async def get_collections(self):
        return types.SimpleNamespace(
            collections=[
                types.SimpleNamespace(name=name) for name in self.vectors_config
            ]
        )

    async def create_collection(self, collection_name, vectors_config):
        self.vectors_config[collection_name] = vectors_config

    async def get_collection(self, collection_name):
        vc = self.vectors_config[collection_name]
        return types.SimpleNamespace(
            config=types.SimpleNamespace(
                params=types.SimpleNamespace(
                    vectors=types.SimpleNamespace(size=vc.size, distance=vc.distance)
                )
            )
        )

    async def close(self):
        self.cerrado = True


@pytest.fixture(autouse=True)
def singleton_limpio(monkeypatch):
    """Cada test arranca con el singleton global virgen (y lo deja virgen)."""
    monkeypatch.setattr(vector_db, "_qdrant_client", None)
    yield


@pytest.mark.asyncio
async def test_ensure_collections_crea_la_topologia_completa(monkeypatch):
    """BUG-011 #107: un dominio por colección de texto + la de imágenes."""
    fake = FakeQdrantClient()

    async def fake_get():
        return fake

    monkeypatch.setattr(vector_db, "get_qdrant_client", fake_get)

    await ensure_collections()

    esperadas = {domain.value for domain in DomainType} | {IMAGE_COLLECTION}
    assert set(fake.vectors_config) == esperadas


@pytest.mark.asyncio
async def test_ensure_collections_es_idempotente(monkeypatch):
    """
    BUG-001 #34 (se mantiene): la segunda pasada no re-crea nada —
    compara y valida la topología preexistente en vez de tocarla.
    """
    fake = FakeQdrantClient()

    async def fake_get():
        return fake

    monkeypatch.setattr(vector_db, "get_qdrant_client", fake_get)

    await ensure_collections()
    topologia_primera = dict(fake.vectors_config)

    await ensure_collections()

    assert fake.vectors_config == topologia_primera, "la segunda pasada re-emitó DDL"


@pytest.mark.asyncio
async def test_close_qdrant_client_cierra_y_resetea(monkeypatch):
    """
    BUG-011 #107, opción A: gemelo del engine.dispose() — el cliente muere
    cerrado y el singleton queda en None para que el serving lo recree.
    """
    fake = FakeQdrantClient()
    monkeypatch.setattr(vector_db, "_qdrant_client", fake)

    await close_qdrant_client()

    assert fake.cerrado is True
    assert vector_db._qdrant_client is None


@pytest.mark.asyncio
async def test_close_qdrant_client_con_singleton_virgen_es_noop():
    """Sin cliente aún inicializado el cierre no explota (bootstrap normal)."""
    await close_qdrant_client()

    assert vector_db._qdrant_client is None


@pytest.mark.asyncio
async def test_bootstrap_core_mcp_reintenta_y_cierra_dentro_del_loop(monkeypatch):
    """
    BUG-011 #107: Qdrant caído en los dos primeros intentos → backoff
    exponencial 1s → 2s → verde. Y la decisión del event loop: el cierre
    viaja con la garantía, DENTRO del mismo loop efímero del bootstrap.
    """
    intentos = {"collections": 0, "cierres": 0}
    sleeps = []

    async def fake_ensure_collections():
        intentos["collections"] += 1
        if intentos["collections"] < 3:
            raise RuntimeError("Qdrant todavía levantándose")

    async def fake_close():
        intentos["cierres"] += 1

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(main_module, "ensure_collections", fake_ensure_collections)
    monkeypatch.setattr(main_module, "close_qdrant_client", fake_close)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    await main_module.bootstrap_collections_with_backoff()

    assert intentos == {"collections": 3, "cierres": 1}
    assert sleeps == [1.0, 2.0], "el backoff debe doblar: 1 → 2 (cap 30)"


@pytest.mark.asyncio
async def test_worker_garantia_arranque_esquema_primero(monkeypatch):
    """
    BUG-011 #107: el worker no consume hasta tener el esquema (reintenta
    con backoff si Postgres está levantándose) y las colecciones van
    DESPUÉS — el claim es lo primero que toca el bucle.
    """
    orden = []
    intentos = {"esquema": 0, "colecciones": 0}
    sleeps = []

    async def fake_esquema():
        orden.append("esquema")
        intentos["esquema"] += 1
        if intentos["esquema"] < 2:
            raise ConnectionError("Postgres levantándose")

    async def fake_colecciones():
        orden.append("colecciones")
        intentos["colecciones"] += 1

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(worker_module, "ensure_relational_schema", fake_esquema)
    monkeypatch.setattr(worker_module, "ensure_collections", fake_colecciones)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    await worker_module.garantia_arranque()

    assert orden == ["esquema", "esquema", "colecciones"]
    assert intentos == {"esquema": 2, "colecciones": 1}
    assert sleeps == [1.0]


class QdrantConCarrera(FakeQdrantClient):
    """
    Boot entero (hallazgo del review de David, 25 nov): otro proceso crea la
    colección ENTRE nuestro get_collections y nuestro create_collection —
    Qdrant responde 409 con la colección ya existiendo y con la topología
    correcta.
    """

    def __init__(self, la_que_choca):
        super().__init__()
        self.la_que_choca = la_que_choca

    async def create_collection(self, collection_name, vectors_config):
        await super().create_collection(collection_name, vectors_config)
        if collection_name == self.la_que_choca:
            # El create nuestro llega tarde: la colección existe (la creó el
            # vecino) y Qdrant contesta 409.
            raise UnexpectedResponse(409)


@pytest.mark.asyncio
async def test_ensure_collections_409_de_carrera_converge_limpio(monkeypatch):
    """
    BUG-011 #107: la idempotencia absorbe la concurrencia del boot entero.
    El 409 del vecino es SEÑAL, no error: se re-valida la topología y el
    arranque termina en verde sin un solo ERROR en el log.
    """
    fake = QdrantConCarrera("document")

    async def fake_get():
        return fake

    monkeypatch.setattr(vector_db, "get_qdrant_client", fake_get)

    await ensure_collections()

    esperadas = {domain.value for domain in DomainType} | {IMAGE_COLLECTION}
    assert set(fake.vectors_config) == esperadas


@pytest.mark.asyncio
async def test_ensure_collection_relanza_lo_que_no_es_409():
    """Un 500 de Qdrant NO es señal idempotente: sube como siempre."""
    fake = FakeQdrantClient()

    async def create_que_revienta(collection_name, vectors_config):
        raise UnexpectedResponse(500)

    fake.create_collection = create_que_revienta

    repo = VectorRepository(client=fake)
    with pytest.raises(UnexpectedResponse):
        await repo.ensure_collection(
            collection_name="document",
            vector_size=TEXT_VECTOR_SIZE,
            distance=TEXT_VECTOR_DISTANCE,
        )
