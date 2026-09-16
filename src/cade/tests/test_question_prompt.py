"""经典 REPL question 工具交互适配器测试。"""

from __future__ import annotations

from unittest.mock import patch

from cade.cli.question_prompt import prompt_questions
from cade.coding_agent.tools.question import CUSTOM_OPTION_LABEL


def test_prompt_questions_maps_single_choice_to_original_label() -> None:
    questions = [
        {
            "question": "Pick one",
            "options": [{"label": "A", "description": "first"}],
        }
    ]

    with patch(
        "cade.cli.question_prompt.safe_select",
        return_value="A - first",
    ):
        assert prompt_questions(questions) == [["A"]]


def test_prompt_questions_cancel_returns_unanswered() -> None:
    questions = [{"question": "Explain"}]

    with patch("cade.cli.question_prompt.safe_text", return_value=None):
        assert prompt_questions(questions) == [[]]


def test_prompt_questions_multiple_custom_answer() -> None:
    questions = [
        {
            "question": "Pick many",
            "multiple": True,
            "options": [{"label": "A"}],
        }
    ]

    with (
        patch(
            "cade.cli.question_prompt.safe_checkbox",
            return_value=[CUSTOM_OPTION_LABEL],
        ),
        patch("cade.cli.question_prompt.safe_text", return_value="custom"),
    ):
        assert prompt_questions(questions) == [["custom"]]
