# Xcode Harness Observability — 审计、全链路追踪与 Hooks

`observability` 模块负责全系统的透明度与可观测性，专注于解决：**如何完整、安全地记录 Agent 的每一个决策与工具执行（含自动凭据脱敏），并为内外部扩展提供强类型的事件流钩子。**

---

## 1. 核心架构与观察体系

```
                   Agent / ToolGate / Provider
                                │
                                ▼
                       HookManager (内部事件)
                                │
        ┌───────────────────────┼───────────────────────┐
        ▼                       ▼                       ▼
 JsonlAuditLogger        RuntimeCorrelation      ExternalHookRunner
(追加写结构化审计)        (全链路追踪 Trace ID)    (执行系统级外部脚本)
        │
        ▼ (自动正则过滤)
    redact_text
 (屏蔽 API Key / Token)
```

### 核心模块与文件
- **审计日志与安全脱敏 ([audit.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/harness/observability/audit.py))**：
  - `JsonlAuditLogger`：以原子追加方式将结构化事件记录到本地 JSONL 审计文件。
  - `redact_text`：在文本进入日志或外部系统前，自动扫描并遮蔽敏感凭据（如 `sk-...`、Authorization Header、私钥等）。
- **全链路追踪上下文 ([correlation.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/harness/observability/correlation.py))**：维护跨会话、跨轮次、跨子代理调用的关联上下文（`RuntimeCorrelation`、`EventCorrelation`）。
- **进程内生命周期 Hooks ([hooks.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/harness/observability/hooks.py))**：提供 `HookManager`，挂载 `BeforeAgentStart`、`BeforeProviderRequest`、`PreToolEvent`、`PostToolEvent` 等生命周期回调。
- **进程外命令钩子 ([external_hooks.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/harness/observability/external_hooks.py))**：驱动外部 Shell 命令（如提交前执行 `git status` 检查或外部审查通知），并收集执行诊断信息。

---

## 2. 架构不变量与设计禁忌

- **架构正交隔离约束**：
  - `observability` 只负责审计记录与事件分发，**严禁重新导出（re-export）** `harness.security` 的权限类型；
  - `security` 模块也仅能调用审计的公共打点接口，两层实现细节互不耦合。
- **审计失败不得阻断主流程**：审计日志写入异常或非关键 External Hook 执行失败，应记录诊断信息而非导致正在运行的业务任务直接崩溃。
