# Cade Memory

本模块实现 `save_memory` 的路径、显式来源、来源尾注、冲突检测和原子提交。
`MemoryCatalogCollector` 在每次请求中注入有界的路径、H1 和紧随的普通段落，
正文仍按需读取。Catalog 是动态 USER_CONTEXT 投影，无独立存储或索引；
不调用模型、不增加工具。目录缺失时返回空上下文，也不创建目录。
用途、调用示例和实现边界统一见 [Cade's Memory](../../../../docs/guide/memory.md)。
