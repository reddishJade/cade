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

## Invariants

Bind to loopback (`127.0.0.1`) by default. Do not expose an unauthenticated service on `0.0.0.0` by default. Web, TUI, and exec all use `build_app()` and the same permission and audit rules.

## Frontend

[static/](static/README.md) contains HTML, CSS, and JavaScript without a build dependency.
