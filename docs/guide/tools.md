# 核心工具箱与执行调度

Cade 内置了一套精简、高效且具备防并发冲突特性的编码工具链。工具层与底层执行调度器协同，确保模型在快速探索的同时，文件写入与命令执行绝对安全。

---

## 1. 内置工具全景表

| 工具名称 | 并发调度类别 | 核心参数 | 功能描述 |
| :--- | :--- | :--- | :--- |
| **`read_file`** | 只读并发池 | `path`, `offset`, `limit` | 读取指定文件内容，支持按行切片和大文件分页读取 |
| **`edit_file`** | 串行写屏障 | `path`, `old_text`, `new_text`, `replace_all` | 基于精准定位与防脏写校验的代码块修改 |
| **`write_file`** | 串行写屏障 | `path`, `content` | 创建全新文件或全量覆写指定文件内容 |
| **`apply_patch`** | 串行写屏障 | `patch` | 兼容标准 unified diff 补丁应用，适合跨多行修改 |
| **`grep_search`** | 只读并发池 | `query`, `path`, `case_sensitive`, `max_results` | 优先通过系统 ripgrep 进行极速全文或正则搜索 |
| **`glob_files`** | 只读并发池 | `pattern`, `path`, `max_results` | 按 Glob 通配符（如 `src/**/*.py`）匹配文件路径 |
| **`find_files`** | 只读并发池 | `pattern`, `path`, `max_results` | 模糊查找包含特定文件名的项目文件 |
| **`list_dir`** | 只读并发池 | `path`, `depth`, `max_entries` | 结构化遍历指定目录下的子文件与目录树 |
| **`bash`** | 串行写屏障 | `command`, `timeout_seconds` | 在 Linux 沙箱或本地隔离环境中执行 Shell 脚本 |
| **`todowrite`** | 状态串行 | `todos: [...]` | 维护结构化待办列表，在 TUI 与上下文中实时展示 |
| **`webfetch`** | 只读并发池 | `url`, `selector` | 抓取指定网页文档内容，提取纯文本或 Markdown |
| **`websearch`** | 只读并发池 | `query` | 调用搜索引擎检索外部最新开源库文档与问题解法 |
| **`question`** | 交互门控 | `question`, `options` | 向人类用户提出结构化交互问题（单选/多选/输入） |
| **`subagent`** | 任务委托 | `task`, `context`, `mode` | 分支独立子代理执行隔离的重构或验证子任务 |

---

## 2. 调度机制：只读并发与写操作屏障

模型在单轮推理中经常会一次性输出多个工具调用（例如同时读取 4 个源文件，或者同时执行搜索）。Cade 的工具执行器（Tools Manager）会自动进行**副作用分类**：

### 2.1 只读并发池（Read Pool）
所有无副作用的工具（如 `read_file`、`grep_search`、`glob_files`、`webfetch` 等）会被自动分发至工作线程池（默认 `tool_workers = 4`）**并行并发执行**。
- 这大幅降低了模型在初步分析项目、收集依赖上下文时的等待耗时。

### 2.2 串行写屏障（Serial Write Barrier）
所有可能改变文件系统状态或产生外部副作用的操作（如 `edit_file`、`write_file`、`apply_patch`、`bash`）会触发**串行屏障**：
- 系统强制等待前面所有的只读并发任务收敛完成；
- 多个写操作之间严格按顺序排队执行，绝不并行，彻底杜绝两个写操作并发修改同一份代码所造成的冲突与乱序。

---

## 3. 防脏写机制：Read-before-edit 精准校验

为了杜绝 Agent 依据过时记忆修改文件导致代码被改坏，`edit_file` 内置了 **Read-before-edit 防脏写机制**：

1. **唯一文本匹配**：`old_text` 必须在目标文件中具备唯一匹配项，避免误伤同名函数或同名代码行；
2. **状态一致性校验**：系统会记录每次 `read_file` 读取时的文件版本；在 `edit_file` 应用修改时，若检测到该文件已经被外部编辑器或用户手动改动过，操作将被安全驳回并提示模型：
   ```text
   File modified since last read. Please read_file again before editing.
   ```
3. 配合快照机制，每次成功的修改均可随时通过 `/undo` 瞬时还原。
