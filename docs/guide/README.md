# Cade 使用指南与开发者手册

欢迎阅读 Cade 官方技术与使用指南。本手册基于当前代码库的真实实现编写，全面涵盖快速上手、核心模式、工具调度、安全沙箱与高级扩展。

---

## 🗺️ 推荐阅读路径

根据你的目标，选择最适合的阅读路线：

### 1. 快速上手（5 分钟上手）
- [安装与环境准备](install.md) —— 系统依赖要求（Python 3.12+、uv、ripgrep、bubblewrap）
- [快速上手指南](quickstart.md) —— 配置 API Key 并完成首个编码与测试任务

### 2. 日常交互与核心概念
- [运行时架构与核心生命周期](architecture.md) —— 统一内核、三端工作台（TUI/CLI/Web）与单回合生命周期
- [执行模式：Plan、Build、Act](modes.md) —— 什么时候调研？什么时候自动修改代码？
- [Slash 命令参考手册](slash-commands.md) —— 30+ 终端指令与日常高频用法
- [CLI 命令行参数与交互指南](cli.md) —— 终端快捷键、@ 文件补全、! 快速执行与非交互模式
- [Web 浏览器工作台](web.md) —— 启动可视化图形界面与 Diff 审查看板

### 3. 会话与安全底层
- [会话管理、快照回滚与上下文换窗](sessions.md) —— JSONL 事实账本、`/undo` 瞬时恢复与 95% 无摘要换窗
- [核心工具箱与执行调度](tools.md) —— 内置工具参数规范、只读并发池与串行写屏障
- [权限、安全与 Linux Sandbox 隔离](security.md) —— Bubblewrap 沙箱防护、只读保护与 Reviewer 审查
- [配置系统完全手册](configuration.md) —— Pydantic 强类型配置字段、默认值与环境变量映射

### 4. 扩展与高级能力
- [模型与 Provider 接入指南](providers.md) —— OpenAI、DeepSeek、GLM、MiMo 与本地私有模型
- [MCP 外部服务与工具扩展](mcp.md) —— 接入 GitHub、PostgreSQL 等外部生态工具
- [Skills 技能扩展系统](skills.md) —— 编写项目专属最佳实践知识库（SKILL.md）
- [长期记忆（Memory）系统](memory.md) —— workspace 中少量可核验知识
- [Subagents 子代理协作机制](subagents.md) —— 长任务分治与独立上下文隔离
- [外部生命周期 Hooks](hooks.md) —— 在关键节点触发自定义脚本与自动化流程

### 5. 版本与维护
- [版本与发布规范](releases.md) —— 语义化版本规则与 CI 自动化发布流程
