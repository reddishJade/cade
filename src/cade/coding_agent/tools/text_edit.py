from __future__ import annotations

import re
from itertools import pairwise


def detect_line_ending(content: str) -> str:
    crlf_idx = content.find("\r\n")
    lf_idx = content.find("\n")
    if lf_idx == -1:
        return "\n"
    if crlf_idx == -1:
        return "\n"
    return "\r\n" if crlf_idx < lf_idx else "\n"


def normalize_to_lf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def restore_line_endings(text: str, ending: str) -> str:
    if ending == "\r\n":
        return text.replace("\n", "\r\n")
    return text


def strip_bom(content: str) -> tuple[str, str]:
    if content.startswith("\ufeff"):
        return ("\ufeff", content[1:])
    return ("", content)


_LINE_ENDINGS = re.compile(r"\r\n|\n|\r")
_FLEXIBLE_NEWLINE = r"(?:\r\n|(?<!\r)\n|\r(?!\n))"


def _replacement_line_endings(content: str, match: re.Match[str], text: str) -> str:
    # 匹配区内的换行符按位置继承；新增换行符沿用邻近行的风格。
    parts = normalize_to_lf(text).split("\n")
    if len(parts) == 1:
        return text

    endings = _LINE_ENDINGS.findall(match.group())
    if endings:
        fallback = endings[-1]
    else:
        following = _LINE_ENDINGS.search(content, match.end())
        if following is not None:
            fallback = following.group()
        else:
            preceding = _LINE_ENDINGS.findall(content[: match.start()])
            fallback = preceding[-1] if preceding else "\n"

    return parts[0] + "".join(
        (endings[i] if i < len(endings) else fallback) + part
        for i, part in enumerate(parts[1:])
    )


def apply_text_replacement(
    content: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> str:
    if old_string == new_string:
        raise ValueError("No changes to apply: oldString and newString are identical.")
    if not old_string:
        raise ValueError(
            "old_string cannot be empty when editing an existing file. "
            "Use write for an intentional full-file replacement."
        )

    # 仅在匹配时将 LF、CRLF 和 CR 视为等价；直接在原始文本上替换。
    lines = normalize_to_lf(old_string).split("\n")
    pattern = re.compile(_FLEXIBLE_NEWLINE.join(re.escape(line) for line in lines))
    first = pattern.search(content)
    if first is None:
        raise ValueError(
            "Could not find old_string in the file. "
            "Whitespace must match exactly; line-ending styles may differ."
        )
    if not replace_all and pattern.search(content, first.start() + 1) is not None:
        raise ValueError(
            "Found multiple matches for old_string. "
            "Provide more surrounding context to make the match unique."
        )

    return pattern.sub(
        lambda match: _replacement_line_endings(content, match, new_string),
        content,
        count=0 if replace_all else 1,
    )


def apply_text_edits(
    content: str,
    edits: tuple[tuple[str, str], ...],
) -> str:
    """在原始内容中定位所有修改，检查冲突后一次性应用。"""
    if not edits:
        raise ValueError("edits must not be empty")
    matched: list[tuple[re.Match[str], str]] = []
    for index, (old_text, new_text) in enumerate(edits):
        if not old_text:
            raise ValueError(f"edits[{index}].old_text must not be empty")
        if old_text == new_text:
            raise ValueError("No changes to apply")
        pieces = normalize_to_lf(old_text).split("\n")
        pattern = re.compile(_FLEXIBLE_NEWLINE.join(re.escape(part) for part in pieces))
        first = pattern.search(content)
        if first is None:
            raise ValueError(f"Could not find edits[{index}].old_text")
        if pattern.search(content, first.start() + 1) is not None:
            raise ValueError(f"Found multiple matches for edits[{index}].old_text")
        matched.append((first, new_text))

    matched.sort(key=lambda item: item[0].start())
    for (previous, _), (current, _) in pairwise(matched):
        if previous.end() > current.start():
            raise ValueError("Edit ranges overlap; merge changes into one entry")

    updated = content
    for match, new_text in reversed(matched):
        replacement = _replacement_line_endings(content, match, new_text)
        updated = updated[: match.start()] + replacement + updated[match.end() :]
    if updated == content:
        raise ValueError("No changes to apply")
    return updated
