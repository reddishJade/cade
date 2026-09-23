"""审计和工具输出脱敏不得破坏带引号的源码赋值。"""

from cade.harness.observability.audit import redact_text


def test_redaction_preserves_assignment_syntax() -> None:
    source = 'auth: { apiKey: "example-test-key", mode: "env" }'

    redacted = redact_text(source)

    assert redacted == 'auth: { apiKey: "[REDACTED]", mode: "env" }'
    assert "example-test-key" not in redacted


def test_redaction_preserves_equals_and_single_quotes() -> None:
    source = "api_key = 'example-test-key'"

    assert redact_text(source) == "api_key = '[REDACTED]'"
