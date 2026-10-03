# 运行时阅读入口

[Cade 架构](../architecture.md) 统一说明组件、任务生命周期、状态归属、权限边界
和代码入口。TUI、exec、浏览器工作台与 Python 调用共享应用和 harness，用户
输入、运行事件、会话记录和宿主操作的关系在该文档中展开。

日常操作按主题查阅 [执行模式](modes.md)、[会话与撤销](sessions.md)、
[配置](configuration.md) 和 [命令参考](slash-commands.md)。预算算法与请求审计
见 [上下文策略](../context-policy.md)，Memory 的证据与存储规则见
[Cade's Memory](../memory.md)。

修改实现前可从 [代码入口表](../architecture.md#修改代码的入口) 定位职责层，
行为验证要求见 [测试指南](../testing.md)。
