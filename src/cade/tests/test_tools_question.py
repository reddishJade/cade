"""交互问题工具纯函数单元测试。"""

from __future__ import annotations

import pytest

from cade.coding_agent.tools.question import (
    _format_answers,
    _questions,
    build_question_tool,
    choice_label,
    set_question_prompt_handler,
)


class TestQuestions:
    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            _questions([])

    def test_minimal_question(self) -> None:
        result = _questions([{"question": "Proceed?"}])
        assert len(result) == 1
        assert result[0]["question"] == "Proceed?"
        assert result[0]["multiple"] is False

    def test_with_options(self) -> None:
        result = _questions(
            [
                {
                    "question": "Pick?",
                    "options": [{"label": "Yes"}, {"label": "No"}],
                }
            ]
        )
        assert len(result[0]["options"]) == 2

    def test_long_header_raises(self) -> None:
        with pytest.raises(ValueError, match="1-30"):
            _questions([{"question": "Q?", "header": "x" * 50}])

    def test_empty_question_text_raises(self) -> None:
        with pytest.raises(ValueError, match="required"):
            _questions([{"question": ""}])

    def test_options_with_descriptions(self) -> None:
        result = _questions(
            [
                {
                    "question": "Q?",
                    "options": [{"label": "A", "description": "Option A"}],
                }
            ]
        )
        assert result[0]["options"][0]["description"] == "Option A"


class TestChoiceLabel:
    def test_with_description(self) -> None:
        assert " - " in choice_label("A", "desc")

    def test_without_description(self) -> None:
        assert choice_label("A", None) == "A"


class TestPromptHandler:
    def test_requires_frontend_handler(self) -> None:
        tool = build_question_tool()

        result = tool.handler({"questions": [{"question": "Proceed?"}]})

        assert "without an interactive prompt handler" in result

    def test_delegates_to_frontend_handler(self) -> None:
        tool = build_question_tool()
        assert set_question_prompt_handler(tool, lambda _questions: [["Yes"]])

        result = tool.handler({"questions": [{"question": "Proceed?"}]})

        assert "Yes" in result


class TestFormatAnswers:
    def test_single_answer(self) -> None:
        result = _format_answers([{"question": "Proceed?"}], [["Yes"]])
        assert "Proceed?" in result
        assert "Yes" in result

    def test_unanswered(self) -> None:
        result = _format_answers([{"question": "Q?"}], [[]])
        assert "Unanswered" in result
