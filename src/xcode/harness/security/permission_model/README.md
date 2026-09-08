# Xcode Harness Security Permission Model — 细粒度权限领域模型

本子包定义了权限系统的底层领域对象、求值器流水线与授权记录存储，专注于解决：**如何将多样化、非结构化的工具调用参数精确抽象为统一的操作意图（Action）、目标实体（Target）与安全策略规则（Rule）。**

---

## 1. 核心实体与求值流水线

```
      Tool Name + Arguments
                │
                ▼
         ActionExtractor ───► 抽取 Action (READ/WRITE/EXEC) + Target (Path/Cmd)
                │
                ▼
      PolicyEvaluator 评估流水线
    ┌───────────┼───────────┬───────────┐
    ▼           ▼           ▼           ▼
  Static   PathBoundary   Shell      Structured
  Policy     Policy      Analysis     Boundary
    │           │           │           │
    └───────────┴─────┬─────┴───────────┘
                      ▼
              PermissionResolver ◄─── GrantStore (已批准凭证)
                      │
                      ▼
                   Verdict (ALLOW / DENY / REVIEW)
```

### 核心模块职责
- **实体定义 ([types.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/harness/security/permission_model/types.py))**：定义 `Action`、`Target`、`Rule`、`Constraint` 与 `Verdict` 核心领域枚举与数据类。
- **意图抽取 ([action.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/harness/security/permission_model/action.py))**：从工具入参中提取资源指纹（`TargetFingerprint`）并计算审批候选对象（`compute_approval_candidate`）。
- **策略求值器 ([evaluators.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/harness/security/permission_model/evaluators.py))**：
  - `StaticPolicyEvaluator`：评估只读/内置工具的基本权限；
  - `PathBoundaryPolicyEvaluator`：判定目标路径是否超出工作区法定边界；
  - `ShellAnalysisPolicyEvaluator`：结合 Shell 语法树分析结果进行风险判定。
- **授权凭证存储 ([stores.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/harness/security/permission_model/stores.py))**：
  - `InMemoryGrantStore`：针对单次运行或非持久授权的内存管理；
  - `FileGrantStore`：将用户持久化允许（Always）的授权规则落盘保存。
- **决策解析器 ([resolver.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/harness/security/permission_model/resolver.py))**：`PermissionResolver` 调度流水线并汇总裁决结果。

---

## 2. 架构不变量与设计禁忌

- **无副作用求值**：求值器（Evaluators）必须是纯函数或无内部副作用的评估器，严禁在评估过程中私自持久化或修改外部授权。
- **指纹确定性**：目标资源指纹计算必须规范化（如绝对路径解析、跨平台路径大小写抹平），避免因路径表达形式不同导致权限规则被绕过。
