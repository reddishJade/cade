"""验证异步工作线程、MCP 状态和统一脱敏的运行时契约。"""

from __future__ import annotations

import pytest

from cade.harness.agent_runtime.async_worker import IsolatedAsyncWorker
from cade.harness.mcp.tools import McpRuntimeRegistry
from cade.harness.observability.audit import redact_json, redact_text


def test_worker_completes_coroutine_and_rejects_submit_after_close() -> None:
    """异步工作线程返回结果，关闭后拒绝新协程。"""

    worker = IsolatedAsyncWorker(name="cade-test-runtime-alignment")

    async def calculate() -> int:
        return 42

    try:
        assert worker.submit(calculate()).result(timeout=3) == 42
    finally:
        worker.close()

    with pytest.raises(RuntimeError, match="worker is closed"):
        worker.submit(calculate())


def test_mcp_runtime_registry_status_from_single_snapshot() -> None:
    """服务器状态由同一份配置和发现快照生成。"""

    runtime = McpRuntimeRegistry()
    runtime.update_runtime_snapshot(
        raw_servers={"demo": {"enabled": True}},
        validated_servers={},
        tool_counts={"demo": 3},
        cache_metadata={},
        server_errors={},
    )
    statuses = runtime.status_snapshot()
    assert len(statuses) == 1
    assert statuses[0].server_name == "demo"
    assert statuses[0].tool_count == 3
    assert statuses[0].state == "failed"
    runtime.close()


def test_shared_redaction_recursively_scrubs_diagnostics() -> None:
    """日志文本与嵌套的 MCP 元数据使用相同的脱敏规则。"""

    secret = "abcdef1234567890"
    text = f"Bearer {secret}"
    assert secret not in redact_text(text)
    nested = {"result": [f"token={secret}", {"message": text}]}
    assert secret not in str(redact_json(nested))
