# Xcode Harness Execution Env — 执行环境与沙箱

`execution_env` 负责底层操作系统交互与环境隔离，专注于解决：**如何安全、可靠地执行本地 Shell 命令与文件系统读写，并在支持的 Linux 系统上通过 Bubblewrap 提供强物理隔离。**

---

## 1. 核心架构与沙箱模型

```
                             Command / File Action
                                       │
                                       ▼
                              CommandSandbox 抽象
                                       │
            ┌──────────────────────────┴──────────────────────────┐
            ▼ (Linux + bwrap 可用)                                ▼ (其他平台 / 降级)
  LinuxBubblewrapSandbox                                    SubprocessShell
  - 项目根目录: 读写绑定 (bind rw)                           - 直接通过操作系统
  - /tmp: 读写绑定                                            异步子进程派生
  - 宿主系统其他路径: 严格只读 (ro-bind)
  - 凭据路径 (~/.ssh, ~/.aws): 屏蔽阻断
  - 网络隔离: 根据 NetworkAccess 策略限制
```

### 核心文件与职责
- **文件系统抽象 ([filesystem.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/harness/execution_env/filesystem.py))**：定义 `FileSystem` 契约与 `LocalFileSystem`，规范文件路径检查与读写接口。
- **Shell 执行协议 ([shell.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/harness/execution_env/shell.py) / [subprocess.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/harness/execution_env/subprocess.py))**：定义 `Shell` 执行契约，`SubprocessShell` 负责超时控制、环境变量透传与实时流捕获。
- **沙箱模型 ([sandbox.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/harness/execution_env/sandbox.py))**：定义 `SandboxPolicy`、`SandboxMode`、`NetworkAccess` 与 `SandboxedCommand` 数据结构。
- **Linux Bubblewrap 沙箱 ([linux_sandbox.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/harness/execution_env/linux_sandbox.py))**：基于 Linux `bwrap` 命名空间技术的轻量级沙箱实现，隔离文件系统可写区域与外部网络。

---

## 2. 架构不变量与设计禁忌

- **审批与隔离正交**：审批策略（`PermissionEngine`）负责向用户展示风险并获得授权；沙箱隔离（`bwrap`）负责物理环境防护。两者独立配置、互不替代。
- **凭据路径绝对不可读**：在 Linux 沙箱模式下，用户的个人私钥（`~/.ssh`）、云凭据（`~/.aws`、`~/.kube`）必须被挂载为黑洞或禁止读取。
- **优雅降级**：非 Linux 环境或系统未安装 `bwrap` 时，必须抛出 `SandboxUnavailableError` 并允许降级为带安全审查的本地直接执行。
