# 核心工具箱与执行调度

Cade 内置了一套精简、高效且具备防并发冲突特性的编码工具链。工具层与底层执行调度器协同，确保模型在快速探索的同时，文件写入与命令执行绝对安全。

---

## 1. 内置工具全景表

| 工具名称 | 并发调度类别 | 核心参数 | 功能描述 |
| :--- | :--- | :--- | :--- |
| **`read`** | 只读并发池 | `path`, `offset`, `limit` | 读取指定文件内容，支持按行切片和大文件分页读取 |
| **`edit`** | 串行写屏障 | `path`, `old_text`, `new_text`, `replace_all` | 基于精准定位与防脏写校验的代码块修改 |
| **`write`** | 串行写屏障 | `path`, `content` | 创建全新文件或全量覆写指定文件内容 |
| **`grep`** | 可选只读并发工具 | `pattern`, `path`, `glob`, `max_results`, `ignore_case`, `literal`, `context` | 优先通过系统 ripgrep 进行极速全文或正则搜索 |
| **`glob`** | 可选只读并发工具 | `pattern`, `path`, `max_results` | 按 Glob 通配符（如 `src/**/*.py`）匹配文件路径 |
| **`find`** | 可选只读并发工具 | `pattern`, `path`, `max_results` | 模糊查找包含特定文件名的项目文件 |
| **`ls`** | 可选只读并发工具 | `path`, `limit` | 结构化遍历指定目录下的子文件与目录树 |
| **`bash`** | 串行执行 | `command`, `timeout_ms`, `workdir`, `purpose` | 在 Linux 沙箱或本地隔离环境中执行 Shell 脚本 |
| **`webfetch`** | 只读并发池 | `url`, `selector` | 抓取指定网页文档内容，提取纯文本或 Markdown |
| **`websearch`** | 只读并发池 | `query` | 调用搜索引擎检索外部最新开源库文档与问题解法 |
| **`question`** | 交互门控 | `question`, `options` | 向人类用户提出结构化交互问题（单选/多选/输入） |
| **`delegate`** | 任务委托 | `description`, `prompt`, `mode` / `session_id` / `tasks` | 创建、续接或批量运行 session-backed child agent |
| **`history`** | 串行只读工具 | `operation`, `entry_id`, `session_id`, `offset`, `max_chars` | 搜索当前 branch；按显式 Session/entry 引用读取原始事件和祖先邻域，不切换当前 head |
| **`save_memory`** | 串行写屏障 | `path`, `markdown`, `sources`, `expected_content` | 校验显式历史来源，原子保存 `.cade/memory/` 中的 Markdown；来源存在不代表结论正确 |

---

## 2. 调度机制：只读并发与写操作屏障

模型在单轮推理中经常会一次性输出多个工具调用（例如同时读取 4 个源文件，或者同时执行搜索）。Cade 的工具执行器（Tools Manager）会自动进行**副作用分类**：

### 2.1 只读并发池（Read Pool）
`ToolSpec.execution_mode="parallel"` 的工具才会进入并发批次。当前明确标记为 parallel 的是 `read` 以及可选的 `grep/glob/find/ls`；其余工具默认 sequential。
- 这避免把“工具是否只读”的猜测散落到调度器里，调度语义由工具契约显式声明。

### 2.2 串行写屏障（Serial Write Barrier）
未声明 parallel 的工具（包括 `edit`、`write`、`bash`）保持 sequential：
- 系统强制等待前面所有的只读并发任务收敛完成；
- 多个写操作之间严格按顺序排队执行，绝不并行，彻底杜绝两个写操作并发修改同一份代码所造成的冲突与乱序。

---

## 3. 防脏写机制：Read-before-edit 精准校验

为了杜绝 Agent 依据过时记忆修改文件导致代码被改坏，`edit` 内置了 **Read-before-edit 防脏写机制**：

1. **唯一文本匹配**：`old_text` 必须在目标文件中具备唯一匹配项，避免误伤同名函数或同名代码行；
2. **状态一致性校验**：系统会记录每次 `read` 读取时的文件版本；在 `edit` 应用修改时，若检测到该文件已经被外部编辑器或用户手动改动过，操作将被安全驳回并提示模型：
   ```text
   File modified since last read. Please read again before editing.
   ```
3. 配合快照机制，每次成功的修改均可随时通过 `/undo` 瞬时还原。
