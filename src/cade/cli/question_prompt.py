"""经典 REPL 的 question 工具交互适配器。"""

from __future__ import annotations

from typing import Any

from cade.coding_agent.tools.question import CUSTOM_OPTION_LABEL, choice_label

from .ptk_patch import safe_checkbox, safe_select, safe_text


def prompt_questions(questions: list[dict[str, Any]]) -> list[list[str]]:
    """使用独立终端提示依次收集 question 工具回答。"""
    answers: list[list[str]] = []
    for item in questions:
        header = item.get("header")
        message = item["question"]
        options = item.get("options")
        if not options:
            selected = safe_text(message, qmark=header or "?")
            answers.append([str(selected)] if selected else [])
            continue

        display_to_label = {
            choice_label(option["label"], option.get("description")): option["label"]
            for option in options
        }
        choices = [*display_to_label, CUSTOM_OPTION_LABEL]
        if item.get("multiple"):
            selected = safe_checkbox(message, choices=choices)
            if selected and CUSTOM_OPTION_LABEL in selected:
                custom = safe_text("Your answer:", qmark=header or "?")
                answers.append([str(custom)] if custom else [])
            else:
                answers.append(
                    [display_to_label[str(value)] for value in (selected or [])]
                )
            continue

        selected = safe_select(message, choices=choices)
        if selected == CUSTOM_OPTION_LABEL:
            custom = safe_text("Your answer:", qmark=header or "?")
            answers.append([str(custom)] if custom else [])
        else:
            answers.append(
                [display_to_label[str(selected)]] if selected is not None else []
            )
    return answers
