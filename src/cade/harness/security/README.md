# Cade Harness Security — 权限引擎与安全审查流水线

`security` 模块是 Cade 运行时的安全防护中枢，专注于解决：**如何根据当前执行模式与策略规则，精确判定工具调用权限；如何对高危 Shell 命令进行语法级语义分析；以及如何优雅协调人机审批（HITL）与模型自动审查。**

---

## 1. 核心架构与决策流水线

```
                      ToolCall 参数与目标
                               │
                               ▼
                        PermissionEngine
                               │
          ┌────────────────────┴────────────────────┐
          ▼                                         ▼
   静态与边界策略求值                         Shell 语法语义分析
(PathBoundary / Sensitive)                  (shell_analyzer.py)
          │                                         │
          └────────────────────┬────────────────────┘
                               ▼
                    规则匹配器 (findLast 覆盖)
                               │
                               ▼
        ┌──────────────────────────────────────────────┐
        │  决策 Verdict (ALLOW / DENY / REVIEW)        │
        └──────────────────────┬───────────────────────┘
                               │ (遇 REVIEW 需审批)
                               ▼
            ┌──────────────────┴──────────────────┐
            ▼ (Act 模式)                          ▼ (Build 模式)
     HITL 交互式确认弹窗                   AutoApprovalReviewer
     (Once / Always / Deny)                (独立模型安全审查)
            │                                     │
            └──────────────────┬──────────────────┘
                               ▼
                     授权记录落盘 (GrantStore)
```

### 核心模块与文件
- **权限引擎核心 ([permissions.py](file:///C:/Users/dwei/workspace/cade/src/cade/harness/security/permissions.py))**：`PermissionEngine` 综合当前执行模式、已记录授权凭证与求值器流水线，给出最终裁决。
- **Shell 语义分析器 ([shell_analyzer.py](file:///C:/Users/dwei/workspace/cade/src/cade/harness/security/shell_analyzer.py))**：
  - 支持 POSIX Bash、PowerShell 与 CMD 命令语法树解析；
  - 自动提取命令读写的具体文件、重定向目标及潜在危险动作（删除、环境变量篡改、网络外发等）。
- **审批流水线 ([approval.py](file:///C:/Users/dwei/workspace/cade/src/cade/harness/security/approval.py) / [approval_reviewer.py](file:///C:/Users/dwei/workspace/cade/src/cade/harness/security/approval_reviewer.py))**：
  - `HITLDecision` / `HITLResult`：人机协同审批回调；
  - `AutoApprovalReviewer`：Build 模式下调动独立的大模型对越界或写操作执行非交互式自动审查。
- **规则匹配引擎 ([rule_matcher.py](file:///C:/Users/dwei/workspace/cade/src/cade/harness/security/rule_matcher.py))**：支持 `findLast` 倒序命中语义，支持模式通配符与特定工具覆盖。

---

## 2. 架构不变量与设计禁忌

- **无物理隔离假定**：权限分析旨在辅助用户认知潜在风险与拦截越界操作，不构成操作系统级的绝对沙箱隔离（隔离必须依赖外部环境或 `bwrap`）。
- **规则覆盖确定性**：规则匹配严格按照“后定义优先（findLast）”原则执行，确保用户本地配置可以精确覆盖默认全局规则。
- **正交依赖**：权限逻辑不依赖 `observability` 的实现细节；授权凭证存储由独立的 `GrantStore` 负责。

---

## 3. 子模块分工

- **[permission_model/](file:///C:/Users/dwei/workspace/cade/src/cade/harness/security/permission_model/README.md)**：细粒度权限领域实体、求值器接口（`PolicyEvaluator`）与凭证存储实现。
