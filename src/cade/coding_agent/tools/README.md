# Cade Coding Agent Tools — 核心工程工具集

本目录包含 Coding Agent 执行真实软件工程任务的全套内置工具，专注于解决：**如何为 Agent 提供具备并发控制、副作用感知、沙箱保护与精确指纹校验的高可用代码读写与执行环境。**

---

## 1. 工具矩阵与职责分类

```
                              ToolSpec (契约与元数据)
                                       │
            ┌──────────────────────────┼──────────────────────────┐
            ▼                          ▼                          ▼
      [并发只读工具]              [串行写操作工具]             [人机与系统工具]
- read_file                - write_file                - bash (沙箱执行)
- glob_files / find_files  - edit_file (SHA256指纹)    - question (交互确认)
- grep_search              - apply_patch               - subagent (任务委派)
- websearch / webfetch     - todowrite                 - cygpath (路径转换)
```

### 工具文件明细
- **代码读写与精准编辑**：
  - `read_file.py`：安全读取文件内容，支持分片与行范围。
  - `write_file.py`：创建新文件或覆写已有文件。
  - `file_handlers.py`：实现核心的 `edit_file`。强制校验 **SHA256 指纹**，若文件在读取后被外部修改则拒绝写入，彻底根除脏写冲突。
  - `apply_patch.py`：高效解析并应用标准 Unified Diff 补丁。
  - `text_edit.py`：基于行范围和精确匹配的替换辅助引擎。
  - `file_image.py`：多模态图片文件支持与 Base64 提取。
- **文件检索与代码搜索**：
  - `glob_search.py`：高性能文件树通配符检索（`glob_files`、`find_files`）。
  - `grep_search.py`：基于 Ripgrep 的代码内容搜索（`grep_search`）。
  - `file_index.py` / `_search_utils.py`：工程目录索引构建与过滤支持。
- **环境交互与进程执行**：
  - `bash.py`：执行终端命令。自动对接底层 Bubblewrap 沙箱或系统原生 Shell，控制超时与进程组清理。
  - `shell_adapter.py`：命令适配器，连接权限判定与 Shell 执行引擎。
  - `cygpath.py`：针对 Windows 环境下 MSYS/Cygwin/POSIX 风格路径的自动互转。
- **协同与任务流**：
  - `subagent.py`：子代理派发工具，支持多子任务批量并发执行并归集结果。
  - `todowrite.py`：维护当前任务的 TODO 清单，驱动多步骤目标逐步达成。
  - `question.py`：人机交互工具，向用户主动提出选择题或确认事项。
- **外部网络能力**：
  - `webfetch.py`：抓取指定 URL 页面并转为精简 Markdown。
  - `websearch.py`：调用搜索引擎获取外部开发文档与最新技术方案。
- **输出截断与安全护栏**：
  - `truncate.py` / `truncation_cleanup.py`：长输出智能折叠，防止单次工具执行导致上下文 Token 溢出。
  - `output_accumulator.py`：流式输出聚合器。
  - `file_mutation_queue.py`：并发写冲突队列与文件锁防护。

---

## 2. 架构不变量与设计禁忌

- **Read-Before-Edit 铁律**：`edit_file` 工具必须校验文件的预读取 SHA256 指纹；模型未曾读取过的文件严禁直接发起局部编辑。
- **并发与副作用分区**：
  - 只读工具（`read_file`、`grep_search`、`glob_files` 等）声明并发安全，由调度器并行触发。
  - 具有文件写（`write_file`、`edit_file`）或 Shell 执行副作用的工具严格保持单线程串行互斥。
- **统一异常契约**：工具发生 IO 错误、找不到路径或语法错误时，必须返回清晰的文本错误说明，严禁直接抛出未捕获的 Python 异常导致循环终结。
