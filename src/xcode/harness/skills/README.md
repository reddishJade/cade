# Xcode Harness Skills — 技能发现与两阶段激活

本目录负责技能（Skills）系统的全生命周期管理，专注于解决：**如何将特定的领域专长（如工作流指导、代码规范手册）以极低的初始 Token 开销引入系统，并在模型确有需要时按需精确激活。**

---

## 1. 核心架构与两阶段激活机制

```
                      扫描候选目录
          (./.xcode/skills/ 与 ~/.xcode/skills/)
                            │
                            ▼
                     SkillRegistry
                            │
            ┌───────────────┴───────────────┐
            ▼ (Phase 1: 轻量索引注入)         ▼ (Phase 2: 按需动态激活)
  SkillIndexCollector                Agent 判定命中任务
            │                               │
            ▼                               ▼
   向 System Prompt 注入              主动调用 load_skill 工具
   技能名称与单行简述 (极低 Token)                  │
                                            ▼
                                     载入完整 SKILL.md
                                     指令、资源与参考文档
```

### 核心模块职责
- **目录发现 ([discovery.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/harness/skills/discovery.py))**：扫描项目级与用户级目录，按优先级去重覆盖（项目级优先于用户级同名技能）。
- **文件解析 ([parsing.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/harness/skills/parsing.py))**：提取 `SKILL.md` 的 YAML Frontmatter 元数据与正文指令，发现子目录资源与 reference 文档。
- **实体定义 ([models.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/harness/skills/models.py))**：定义 `SkillDef`、`SkillSummary`、`SkillResource` 与 `SkillDiagnostic`。
- **动态注册表 ([registry.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/harness/skills/registry.py))**：`SkillRegistry` 维护已加载技能的缓存与索引。
- **激活工具 ([tools.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/harness/skills/tools.py))**：提供 `load_skill` 工具，执行第二阶段的完整加载。

---

## 2. 架构不变量与设计禁忌

- **两阶段加载约束**：严禁在 Agent 启动阶段无差别将所有技能的完整 Markdown 内容注入系统提示词；必须遵循“首阶段注入摘要，次阶段按需加载正文”的规则。
- **只读纯文本安全性**：技能仅包含文本指南与模式说明，不包含任意可执行二进制代码，自身不破坏沙箱边界。
