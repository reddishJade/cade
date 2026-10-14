"""保护 Linux Bubblewrap 进程沙箱策略包装与隔离参数契约。

控制性回归使用真实 LinuxBubblewrapSandbox 与 SandboxPolicy。
断言空参数拦截、工作目录越界拒绝、网络隔离标志开关及读写模式挂载参数。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from cade.harness.execution_env.linux_sandbox import LinuxBubblewrapSandbox
from cade.harness.execution_env.sandbox import (
    NetworkAccess,
    SandboxMode,
    SandboxPolicy,
)


@pytest.mark.skipif(sys.platform != "linux", reason="Bubblewrap sandbox is Linux only")
def test_sandbox_argv_and_cwd_validation(tmp_path: Path) -> None:
    """验证沙箱拒绝空命令行参数以及逃离项目根目录的 cwd。"""
    policy = SandboxPolicy(project_root=tmp_path)
    sandbox = LinuxBubblewrapSandbox(policy)

    # 1. 空 argv 拒绝
    with pytest.raises(ValueError, match="sandbox command argv must not be empty"):
        sandbox.wrap([], tmp_path)

    # 2. 外部 cwd 拒绝
    outside = tmp_path.parent / "outside"
    outside.mkdir(exist_ok=True)
    with pytest.raises(
        ValueError, match="sandbox command cwd must stay inside the project root"
    ):
        sandbox.wrap(["ls"], outside)

    # 3. 合法调用成功包装
    cmd = sandbox.wrap(["python", "script.py"], tmp_path)
    assert cmd.cwd == tmp_path
    assert "script.py" in cmd.argv


@pytest.mark.skipif(sys.platform != "linux", reason="Bubblewrap sandbox is Linux only")
def test_sandbox_network_isolation_flags(tmp_path: Path) -> None:
    """验证网络策略开关正确映射到 --unshare-net 隔离参数。"""
    # 1. 默认网络阻断 (DENY) 必须包含 --unshare-net
    deny_policy = SandboxPolicy(
        project_root=tmp_path, network_access=NetworkAccess.DENY
    )
    deny_sandbox = LinuxBubblewrapSandbox(deny_policy)
    deny_cmd = deny_sandbox.wrap(["curl", "https://example.com"], tmp_path)
    assert "--unshare-net" in deny_cmd.argv

    # 2. 显式放行网络 (ALLOW) 不得附加 --unshare-net
    allow_policy = SandboxPolicy(
        project_root=tmp_path, network_access=NetworkAccess.ALLOW
    )
    allow_sandbox = LinuxBubblewrapSandbox(allow_policy)
    allow_cmd = allow_sandbox.wrap(["curl", "https://example.com"], tmp_path)
    assert "--unshare-net" not in allow_cmd.argv


@pytest.mark.skipif(sys.platform != "linux", reason="Bubblewrap sandbox is Linux only")
def test_sandbox_filesystem_mode_flags(tmp_path: Path) -> None:
    """验证工作区只读模式 (READ_ONLY) 与可写模式 (WORKSPACE_WRITE) 的挂载参数契约。"""
    # 1. 工作区写模式：必须以可读写 --bind 挂载 project_root
    write_policy = SandboxPolicy(
        project_root=tmp_path, mode=SandboxMode.WORKSPACE_WRITE
    )
    write_sandbox = LinuxBubblewrapSandbox(write_policy)
    write_cmd = write_sandbox.wrap(["touch", "file.txt"], tmp_path)
    argv_str = " ".join(write_cmd.argv)
    assert f"--bind {tmp_path} {tmp_path}" in argv_str

    # 2. 工作区只读模式：根目录与工作区均不应以写 --bind 形式暴露
    ro_policy = SandboxPolicy(project_root=tmp_path, mode=SandboxMode.READ_ONLY)
    ro_sandbox = LinuxBubblewrapSandbox(ro_policy)
    ro_cmd = ro_sandbox.wrap(["ls"], tmp_path)
    ro_argv_str = " ".join(ro_cmd.argv)
    assert f"--bind {tmp_path} {tmp_path}" not in ro_argv_str
