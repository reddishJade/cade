"""Cade Web 服务启动器。"""

from __future__ import annotations

import secrets
from pathlib import Path

from cade.coding_agent.app import CadeApp, build_app
from cade.harness.config import (
    CadeRuntimeConfig,
    discover_runtime_config,
)


def run_web_server(
    project_root: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8787,
    config_path: Path | None = None,
    runtime_config: CadeRuntimeConfig | None = None,
    sessions_dir: Path | None = None,
    open_browser: bool = False,
) -> int:
    """启动 Web 服务；阻塞直到进程退出。"""
    if runtime_config is None:
        runtime_config = discover_runtime_config(project_root, config_path)
    app = build_app(
        project_root=project_root,
        runtime_config=runtime_config,
        sessions_dir=sessions_dir,
    )

    def build_for(target: Path) -> CadeApp:
        """为指定工作区重新装配运行时（工作区切换用）。"""
        rc = discover_runtime_config(target, None)
        return build_app(
            project_root=target,
            runtime_config=rc,
        )

    try:
        from .api import create_app
        from .security import web_origins

        token = secrets.token_urlsafe(32)
        origins = web_origins(host, port)
        browser_url = f"{origins[0]}/#token={token}"
        fastapi_app = create_app(
            app,
            project_root,
            app_factory=build_for,
            access_token=token,
            allowed_origins=origins,
        )
        print(f"Open this private URL in your browser: {browser_url}", flush=True)
        if open_browser:
            import threading
            import webbrowser
            from time import perf_counter

            started = perf_counter()

            def _open() -> None:
                # 给 uvicorn 一点启动时间再打开浏览器
                import time as _time

                while perf_counter() - started < 1.5:
                    _time.sleep(0.1)
                webbrowser.open(browser_url)

            threading.Thread(target=_open, daemon=True).start()

        import uvicorn

        uvicorn.run(
            fastapi_app, host=host, port=port, log_level="info", proxy_headers=False
        )
        return 0
    except KeyboardInterrupt:
        return 0
    finally:
        app.close()
