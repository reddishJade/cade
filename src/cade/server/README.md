# Browser workspace service

`server` uses FastAPI and WebSocket to present typed Agent events as reasoning, tool cards, steps, and approvals with bidirectional controls.

## Communication

```text
Browser
  → REST: sessions, settings, workspaces
  ↔ WebSocket /ws: events, user input, approvals
  → FastAPI api.py
  → WebRunHub in runner.py
  → CadeApp
```

### Files

- [api.py](api.py): REST routes and persistent bidirectional WebSocket connections.
- [runner.py](runner.py): dispatch requests to `CadeApp`, cancel and queue work, broadcast state.
- [serve.py](serve.py): `run_web_server`, Uvicorn startup, ports, application reload, and `--open` browser launch.
- [serialize.py](serialize.py): convert `AgentHarnessEvent` and domain values to JSON.
- [security.py](security.py): enforce trusted hosts, origins, and token authentication.

## Invariants

Bind to loopback (`127.0.0.1`) by default. Validate Host and Origin before routing HTTP requests or accepting WebSocket connections. API and WebSocket access requires a per-start token from the private startup URL; see [access control](../../../docs/web.md#access-control). Web, TUI, and exec all use `build_app()` and the same permission and audit rules.

## Frontend

[static/](static/README.md) contains HTML, CSS, and JavaScript without a build dependency.
