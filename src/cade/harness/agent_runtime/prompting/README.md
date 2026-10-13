# Cade Harness Prompting — 运行时前缀

[build_runtime_context_provider](builder.py) 在装配时生成身份和规则，
并捕获工作区根、Shell 与环境信息。返回的回调在每次请求读取当前目录，
从当次启用的工具集合生成操作指南，按模块选择拼接前缀，并保留分界标记。

[tools.py](tools.py) 生成操作指南；工具名称、描述和参数由 API schema 声明。
[citations.py](citations.py) 为请求消息补充事实出处的引用格式。
目录条目的展示上限由前缀构建入口控制。

项目指令、NOTE、验证事实和技能目录由上下文 collector 提供。
完整请求的预算准入与换窗属于 RequestAssembler 和 ContextPolicy，见
[架构说明](../../../../../docs/architecture.md)。
