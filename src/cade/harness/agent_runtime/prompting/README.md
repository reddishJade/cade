# Runtime prefixes

[build_runtime_context_provider](builder.py) generates identity and rules at assembly and captures workspace root, shell, and environment. Its callback builds guidance from the enabled tools, joins selected prefix modules, and retains boundary markers. The `cwd` section recollects directory entries for each request. Changed entries append factual context snapshots; they do not rewrite system instructions.

[tools.py](tools.py) generates operational guidance. API schemas declare tool names, descriptions, and parameters. [citations.py](citations.py) supplies factual source citation formatting. The prefix builder controls directory display limits.

Context collectors supply project instructions, NOTE, verification facts, and skill catalogs. RequestAssembler and ContextPolicy own complete-request admission and rollover. See the [architecture](../../../../../docs/architecture.md).
