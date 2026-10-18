"""Session 持久化 schema 的共享版本定义。"""

SESSION_EVENT_SCHEMA_VERSION = 3


def supports_session_event_schema(version: object) -> bool:
    """版本 3 增加运行时上下文事实，仍可读取不含该事件的版本 2。"""
    return isinstance(version, int) and version in (2, SESSION_EVENT_SCHEMA_VERSION)
