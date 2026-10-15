# Memory

This module implements `save_memory` path checks, explicit sources, source footers, conflict detection, and atomic commits.

`MemoryCatalogCollector` injects bounded paths, H1 headings, and opening paragraphs on each request. Bodies remain on demand. The catalog is a dynamic USER_CONTEXT projection with no separate storage or index. It makes no model calls and adds no tools. A missing directory returns empty context without creating it.

See [Cade's Memory](../../../../docs/guide/memory.md) for purpose, examples, and implementation boundaries.
