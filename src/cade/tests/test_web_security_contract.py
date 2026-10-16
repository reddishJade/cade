"""保护 Web 控制面：陌生来源、重绑定主机和无令牌客户端不能读取或控制 agent。

使用真实 FastAPI 路由、中间件和 WebRunHub，仅替换不应触发模型或磁盘写入的应用边界。
运行：uv run --locked pytest -q src/cade/tests/test_web_security_contract.py。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from cade.coding_agent.app import CadeApp
from cade.server.api import create_app
from cade.server.security import web_origins

ORIGIN = "http://127.0.0.1:8787"
WS_URL = "ws://127.0.0.1:8787/ws"
TOKEN = "test-only-web-access-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def make_app(root: Path, token: str = TOKEN) -> FastAPI:
    app = cast(
        CadeApp,
        SimpleNamespace(
            session_store=SimpleNamespace(session_id="test-session", project_root=root),
            get_model_info=dict,
            mcp_status=list,
        ),
    )
    return create_app(app, root, access_token=token)


@pytest.fixture
def server() -> FastAPI:
    return make_app(Path.cwd())


def test_http_requires_token_and_rejects_rebinding(server: FastAPI) -> None:
    with TestClient(server, base_url=ORIGIN) as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/info").status_code == 401
        assert (
            client.get(
                "/api/info", headers={"Authorization": "Bearer wrong"}
            ).status_code
            == 401
        )
        assert (
            client.get("/api/info", headers=AUTH).json()["session_id"] == "test-session"
        )
        assert client.get("/api/info?token=" + TOKEN).status_code == 401
        assert (
            client.get(
                "/api/info", headers={**AUTH, "Origin": "http://evil.test"}
            ).status_code
            == 403
        )
        for host in ("evil.test:8787", "127.0.0.1:9000", "127.0.0.1.evil.test:8787"):
            assert (
                client.get("/api/info", headers={**AUTH, "Host": host}).status_code
                == 400
            )
        assert client.get("/", headers={"Host": "evil.test:8787"}).status_code == 400
        assert client.get("/openapi.json", headers=AUTH).status_code == 404
        assert (
            client.get("/").headers["content-security-policy"]
            == "frame-ancestors 'none'"
        )


@pytest.mark.parametrize(
    "path",
    [
        "/api/workspaces",
        "/api/model",
        "/api/git/branches",
        "/api/sessions",
        "/api/sessions/resume",
    ],
)
@pytest.mark.parametrize(
    "origin",
    [
        None,
        "null",
        "http://evil.test",
        "https://127.0.0.1:8787",
        "http://127.0.0.1:9000",
    ],
)
def test_mutations_require_trusted_origin(
    server: FastAPI, path: str, origin: str | None
) -> None:
    headers = dict(AUTH)
    if origin is not None:
        headers["Origin"] = origin
    with TestClient(server, base_url=ORIGIN) as client:
        assert client.post(path, json={}, headers=headers).status_code == 403


def test_workspace_denial_has_no_side_effects(server: FastAPI) -> None:
    with TestClient(server, base_url=ORIGIN) as client:
        before = server.state.hub.app
        assert (
            client.post(
                "/api/workspaces",
                json={"path": str(Path.cwd())},
                headers={"Origin": ORIGIN},
            ).status_code
            == 401
        )
        assert server.state.hub.app is before
        response = client.post(
            "/api/workspaces", json={}, headers={**AUTH, "Origin": ORIGIN}
        )
        assert response.status_code == 400
        assert response.json()["error"] == "Workspace switching is not enabled"


@pytest.mark.parametrize(
    ("headers", "protocols"),
    [
        ({"Origin": ORIGIN}, []),
        ({"Origin": ORIGIN}, ["cade", "wrong"]),
        ({}, ["cade", TOKEN]),
        ({"Origin": "null"}, ["cade", TOKEN]),
        ({"Origin": "http://evil.test"}, ["cade", TOKEN]),
        ({"Origin": ORIGIN, "Host": "evil.test:8787"}, ["cade", TOKEN]),
        ({"Origin": ORIGIN, "Host": "127.0.0.1:9000"}, ["cade", TOKEN]),
    ],
)
def test_websocket_denied_before_hub_attach(
    server: FastAPI,
    headers: dict[str, str],
    protocols: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attached: list[object] = []
    monkeypatch.setattr(server.state.hub, "attach", attached.append)
    with TestClient(server, base_url=ORIGIN) as client:
        with (
            pytest.raises(WebSocketDisconnect) as error,
            client.websocket_connect(WS_URL, headers=headers, subprotocols=protocols),
        ):
            pytest.fail("Untrusted WebSocket was accepted")
        assert error.value.code == 1008
        assert attached == []


def test_authenticated_websocket_controls(
    server: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    submitted: list[tuple[object, ...]] = []
    approvals: list[dict[str, str]] = []

    def submit(*args: object) -> None:
        submitted.append(args)

    def approve(**kwargs: str) -> bool:
        approvals.append(kwargs)
        return True

    monkeypatch.setattr(server.state.hub, "submit", submit)
    monkeypatch.setattr(server.state.hub, "resolve_approval", approve)
    with (
        TestClient(server, base_url=ORIGIN) as client,
        client.websocket_connect(
            WS_URL, headers={"Origin": ORIGIN}, subprotocols=["cade", TOKEN]
        ) as ws,
    ):
        assert ws.accepted_subprotocol == "cade"
        assert ws.receive_json()["type"] == "hello"
        ws.send_json({"type": "submit", "text": "test task", "mode": "act"})
        ws.send_json({"type": "approval", "id": "pending-id", "decision": "allow"})
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}
    assert submitted == [("test task", "act")]
    assert approvals == [
        {
            "request_id": "pending-id",
            "decision": "allow",
            "scope": "once",
            "suggestion": "",
        }
    ]


def test_old_token_fails_after_restart() -> None:
    with TestClient(make_app(Path.cwd(), "new-token"), base_url=ORIGIN) as client:
        assert client.get("/api/info", headers=AUTH).status_code == 401
        with (
            pytest.raises(WebSocketDisconnect),
            client.websocket_connect(
                WS_URL, headers={"Origin": ORIGIN}, subprotocols=["cade", TOKEN]
            ),
        ):
            pytest.fail("Old token was accepted")


def test_duplicate_security_headers_rejected(server: FastAPI) -> None:
    with TestClient(server, base_url=ORIGIN) as client:
        assert (
            client.get(
                "/api/info", headers=[("Host", "127.0.0.1:8787"), ("Host", "evil.test")]
            ).status_code
            == 400
        )
        assert (
            client.post(
                "/api/sessions",
                headers=[
                    *AUTH.items(),
                    ("Origin", ORIGIN),
                    ("Origin", "http://evil.test"),
                ],
            ).status_code
            == 403
        )
        assert (
            client.get(
                "/api/info", headers=[*AUTH.items(), ("Authorization", "Bearer wrong")]
            ).status_code
            == 401
        )


def test_wildcard_bind_does_not_trust_arbitrary_hosts() -> None:
    assert web_origins("0.0.0.0", 9000) == (
        "http://127.0.0.1:9000",
        "http://localhost:9000",
        "http://[::1]:9000",
    )
    assert web_origins("192.0.2.1", 9000) == ("http://192.0.2.1:9000",)
    assert web_origins("::1", 9000)[0] == "http://[::1]:9000"
    assert web_origins("localhost", 9000)[0] == "http://localhost:9000"
    assert web_origins("LOCALHOST", 80)[0] == "http://localhost"


def test_startup_url_authenticates_and_rotates(
    server: FastAPI, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import uvicorn

    from cade.server import serve

    app = server.state.hub.app
    closed: list[bool] = []
    tokens: list[str] = []
    monkeypatch.setattr(app, "close", lambda: closed.append(True), raising=False)
    monkeypatch.setattr(serve, "build_app", lambda **kwargs: app)
    monkeypatch.setattr(serve, "discover_runtime_config", lambda *args: None)

    def run(application: FastAPI, **kwargs: object) -> None:
        url = capsys.readouterr().out.strip().split()[-1]
        origin, token = url.split("/#token=")
        assert origin == "http://[::1]:9000"
        assert len(token) >= 43
        tokens.append(token)
        assert kwargs["host"] == "::1"
        assert kwargs["port"] == 9000
        assert kwargs["proxy_headers"] is False
        # 测试客户端不支持 IPv6 URL，改用显式 Host 检查 IPv6 来源策略。
        with TestClient(
            application,
            base_url="http://127.0.0.1:9000",
            headers={"Host": "[::1]:9000"},
        ) as client:
            assert client.get("/api/info").status_code == 401
            assert (
                client.get(
                    "/api/info", headers={"Authorization": f"Bearer {token}"}
                ).status_code
                == 200
            )

    monkeypatch.setattr(uvicorn, "run", run)
    for _ in range(2):
        assert serve.run_web_server(Path.cwd(), host="::1", port=9000) == 0
    assert tokens[0] != tokens[1]
    assert closed == [True, True]
