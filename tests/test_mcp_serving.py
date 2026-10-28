"""
Tests del serving dual de la frontera MCP (ADR-008-002, #77).

La vía decidida por el spike ([ADR-008-001] #76): dual-transport con la
surface actual — el mismo `Server` low-level sirve STDIO y streamable HTTP
sin duplicar registro ni handlers. Estos tests defienden:

- tools/list IDÉNTICO por ambos transportes: la MISMA instancia de
  `src.main.mcp_server`, hablada por HTTP real (uvicorn en puerto
  efímero) y por la vía STDIO (streams de memoria con el mismo driver
  `run` del patrón Cline — sin subprocess: el cortafuegos de la conftest
  no viaja en el env saneado de un spawn).
- Round-trip HTTP completo con los handlers de la frontera mockeados
  (initialize → tools/list → dispatch → poll → search): el objeto bajo
  test es el TRANSPORTE, no la política — la capa de datos ya vive en su
  propia suite. Los mocks además evitan cruzar loops de event entre el
  hilo de uvicorn y el pool de asyncpg del hilo de pytest.
- El router estricto por HTTP: una tool desconocida rebota con isError.
"""

import json
import threading
import time
from unittest.mock import AsyncMock, patch

import anyio
import pytest
import uvicorn
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.memory import create_client_server_memory_streams

from src.main import mcp_server, create_initialization_options
from src.schemas.recall import MemoryHit, RecallResponse

EXPECTED_TOOLS = {
    "coreai_dispatch_task",
    "coreai_get_task",
    "coreai_search_context",
    "coreai_search_image",
}

TASK_ID = "2619b8c2-3f4a-4b1e-9c8d-111111111111"
SESSION_ID = "7e4f9a01-8c2b-4d67-a3b5-222222222222"


# =====================================================================
# FIXTURES
# =====================================================================

@pytest.fixture
async def http_server_url():
    """Servidor HTTP REAL contra la instancia de src.main: uvicorn en puerto efímero.

    Cada test levanta su app: session manager fresco, puerto efímero
    (port=0), teardown limpio. El host es localhost para que la protección
    DNS-rebinding del SDK se comporte como en local.
    """
    app = mcp_server.streamable_http_app(host="127.0.0.1")
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True, name="test-http")
    thread.start()

    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if server.started and server.servers:
            break
        time.sleep(0.05)
    assert server.started, "uvicorn no llegó a arrancar"

    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}/mcp"

    server.should_exit = True
    thread.join(timeout=5.0)


def _tool_response(payload: dict) -> dict:
    """Respuesta con la forma cruda que la frontera espera de un handler."""
    return {"content": [{"type": "text", "text": json.dumps(payload)}], "isError": False}


def _is_error(result) -> bool:
    """Atributo de error snake/camel-agnóstico (línea v2 del SDK)."""
    return bool(getattr(result, "is_error", getattr(result, "isError", False)))


def _input_schema(tool) -> dict:
    return getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None)


def _listing_as_dict(listing) -> dict:
    """tools/list como dict comparable: nombre → inputSchema."""
    return {tool.name: _input_schema(tool) for tool in listing.tools}


# =====================================================================
# DoD: tools/list IDÉNTICO por ambos transportes
# =====================================================================

@pytest.mark.asyncio
async def test_tools_list_identico_por_ambos_transportes(http_server_url):
    """
    La MISMA instancia de Server sirve el catálogo completo por HTTP y por
    la vía STDIO, byte a byte: mismos nombres, mismos inputSchema.
    """
    # --- HTTP real: cliente SDK contra el app de uvicorn ---
    async with streamable_http_client(http_server_url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            http_listing = await session.list_tools()

    # --- STDIO-equivalente: mismo driver `run` del patrón Cline sobre
    #     streams de memoria (la misma función que secuestra stdin/stdout) ---
    stdio_listing = None
    async with anyio.create_task_group() as tg:
        async with create_client_server_memory_streams() as (client_streams, server_streams):
            c_read, c_write = client_streams
            s_read, s_write = server_streams
            tg.start_soon(
                mcp_server.run, s_read, s_write, create_initialization_options()
            )
            async with ClientSession(c_read, c_write) as session:
                await session.initialize()
                stdio_listing = await session.list_tools()
            # el cierre de los streams de memoria termina el driver run()
        # el task group espera a que run() vea el EOF

    # Las 4 tools de la frontera, por ambos transportes
    assert {t.name for t in http_listing.tools} == EXPECTED_TOOLS
    assert {t.name for t in stdio_listing.tools} == EXPECTED_TOOLS

    # Y el catálogo es IDÉNTICO: mismo nombre → mismo inputSchema
    assert _listing_as_dict(http_listing) == _listing_as_dict(stdio_listing)


# =====================================================================
# Round-trip HTTP: initialize → tools/list → dispatch → poll → search
# =====================================================================

@pytest.mark.asyncio
async def test_roundtrip_http_dispatch_poll_search(http_server_url):
    """
    El bucle write-then-read completo por la vía HTTP, con los handlers de
    la frontera mockeados: el transporte entrega los argumentos intactos y
    serializa las respuestas tal cual el patrón Cline recibe hoy.
    """
    dispatch_payload = {
        "task_id": TASK_ID,
        "session_id": SESSION_ID,
        "status": "PENDING",
    }
    poll_payload = {
        "task_id": TASK_ID,
        "session_id": SESSION_ID,
        "task_type": "vectorize_event",
        "status": "COMPLETED",
        "error_trace": None,
    }
    search_payload = RecallResponse(
        hits=[
            MemoryHit(
                point_id="33333333-3333-3333-3333-333333333333",
                domain="system",
                score=0.9,
                content="memoria del taller",
                session_id=None,
                project=None,
            )
        ]
    ).model_dump_json(indent=2)

    task_handler = AsyncMock(side_effect=[
        _tool_response(dispatch_payload),
        _tool_response(poll_payload),
    ])
    recall_handler = AsyncMock(return_value=_tool_response(search_payload))

    with patch("src.main.handle_task_tool", new=task_handler), \
         patch("src.main.handle_recall_tool", new=recall_handler):

        async with streamable_http_client(http_server_url) as (read, write):
            async with ClientSession(read, write) as session:
                # 1. Handshake
                await session.initialize()

                # 2. Catálogo completo por el wire
                listing = await session.list_tools()
                assert {t.name for t in listing.tools} == EXPECTED_TOOLS

                # 3. Dispatch (write)
                dispatch_args = {
                    "task_type": "vectorize_event",
                    "task_payload": {"content": "Round-trip HTTP: dispatch desde streamable HTTP."},
                }
                dispatch = await session.call_tool("coreai_dispatch_task", dispatch_args)
                assert not _is_error(dispatch), f"El dispatch falló por HTTP: {dispatch}"

                # 4. Poll acotado (read-then-check)
                poll = await session.call_tool("coreai_get_task", {"task_id": TASK_ID})
                assert not _is_error(poll), f"El poll falló por HTTP: {poll}"

                # 5. Recall (search)
                search = await session.call_tool(
                    "coreai_search_context", {"query": "memorias del taller"}
                )
                assert not _is_error(search), f"El search falló por HTTP: {search}"

    # Argumentos intactos a través del wire: la frontera recibió lo que el
    # cliente envió, sin relleno ni pérdida.
    task_handler.assert_any_await("coreai_dispatch_task", dispatch_args)
    task_handler.assert_awaited_with("coreai_get_task", {"task_id": TASK_ID})
    recall_handler.assert_awaited_once_with(
        "coreai_search_context", {"query": "memorias del taller"}
    )

    # Y las respuestas llegaron serializadas como el patrón Cline espera
    assert json.loads(dispatch.content[0].text) == dispatch_payload
    assert json.loads(poll.content[0].text) == poll_payload
    parsed = RecallResponse.model_validate_json(search.content[0].text)
    assert parsed.hits[0].content == "memoria del taller"
    assert parsed.hits[0].score == 0.9


@pytest.mark.asyncio
async def test_router_estricto_por_http(http_server_url):
    """El router estricto también manda por HTTP: tool desconocida = isError."""
    async with streamable_http_client(http_server_url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("coreai_nonsense", {"x": 1})

    assert _is_error(result)
    assert "Herramienta desconocida: coreai_nonsense" in result.content[0].text
