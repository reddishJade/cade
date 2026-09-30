"""三臂记忆经验评测的聚合统计与 Markdown 报告。

每个任务/重复次数组成一个三元组（none、relevant、irrelevant）。报告只对
三元组内的同臂记录取均值，并在每一行标注该行实际使用的三元组数量；原始
attempt 记录始终保留在结果目录中。
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean
from typing import Any

ARMS: tuple[str, ...] = ("none", "relevant", "irrelevant")

_DELTA_KINDS = {
    "input_tokens_total": "percent",
    "task_success": "points",
    "hint_fired": "points",
    "stale_follow": "points",
    "stale_hint_acted": "points",
}

_METRICS: tuple[tuple[str, str, str], ...] = (
    ("Task success rate", "task_success", "rate"),
    ("Provider calls", "provider_call_count", "number"),
    ("Input tokens", "input_tokens_total", "number"),
    ("Tool calls", "tool_call_count", "number"),
    ("Distinct files read", "distinct_files_read", "number"),
    ("Repeated read calls", "repeated_read_calls", "number"),
    ("Memory hint fired", "hint_fired", "rate"),
    ("Stale anchor followed first", "stale_follow", "rate"),
    ("Stale fix location acted on", "stale_hint_acted", "rate"),
    ("Remember calls", "remember_calls", "number"),
    ("Steps to anchor (diagnostic)", "steps_to_anchor", "number"),
)


@dataclass(frozen=True)
class _Triplet:
    """同一任务与重复次数下的三个实验臂记录。"""

    task_id: str
    repeat: int
    records: dict[str, dict[str, Any]]


def load_memory_records(path: Path) -> list[dict[str, Any]]:
    """读取结果目录中的原始 attempt 记录，忽略 summary.json。"""
    records: list[dict[str, Any]] = []
    for record_path in sorted(path.resolve().glob("*.json")):
        if record_path.name == "summary.json":
            continue
        payload = json.loads(record_path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and payload.get("arm") in ARMS:
            records.append({str(key): value for key, value in payload.items()})
    if not records:
        raise ValueError(f"no memory benchmark records found in {path.resolve()}")
    return records


def summarize_memory_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    """按三元组聚合三臂指标，并为每项指标建立独立 cohort。"""
    grouped: dict[tuple[str, int], dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for record in records:
        arm = str(record.get("arm", ""))
        if arm not in ARMS:
            continue
        task_id = str(record.get("task_id", ""))
        repeat = _integer_field(record, "repeat")
        grouped[(task_id, repeat)][arm].append(record)

    triplets: list[_Triplet] = []
    excluded: list[dict[str, Any]] = []
    for (task_id, repeat), arms in sorted(grouped.items()):
        reasons = [
            f"{arm} records={len(arms.get(arm, []))}"
            for arm in ARMS
            if len(arms.get(arm, [])) != 1
        ]
        if reasons:
            excluded.append(
                {"task_id": task_id, "repeat": repeat, "reason": "; ".join(reasons)}
            )
            continue
        triplets.append(
            _Triplet(
                task_id=task_id,
                repeat=repeat,
                records={arm: arms[arm][0] for arm in ARMS},
            )
        )

    by_task: dict[str, list[_Triplet]] = defaultdict(list)
    for triplet in triplets:
        by_task[triplet.task_id].append(triplet)
    return {
        "schema_version": 1,
        "arms": list(ARMS),
        "runs": len(records),
        "logical_triplets": len(grouped),
        "complete_triplets": len(triplets),
        "metrics": _metric_summary(triplets),
        "coverage": _coverage(triplets),
        "per_task": {
            task_id: {
                "triplets": len(task_triplets),
                "metrics": _metric_summary(task_triplets),
            }
            for task_id, task_triplets in sorted(by_task.items())
        },
        "excluded_triplets": excluded,
    }


def render_markdown(summary: dict[str, Any]) -> str:
    """渲染含每行 cohort 的 Markdown 报告。"""
    lines = [
        "# Memory experience benchmark (three arms)",
        "",
        (
            f"Runs: {summary['runs']} attempt records, "
            f"{summary['complete_triplets']} complete task/repeat triplets "
            f"out of {summary['logical_triplets']} logical triplets."
        ),
        "",
        (
            "Arms: `none` seeds no `MEMORY.md`; `relevant` seeds an experience that "
            "matches the fixture failure; `irrelevant` seeds an unrelated decoy "
            "experience plus a stale experience that fires on the real anchor with a "
            "wrong fix location."
        ),
        "",
        "## All tasks",
        "",
        _render_metric_table(summary["metrics"]),
        "",
        (
            "Each row uses only the triplets where all three arms report that metric, "
            "so the Cohort column can differ per row. Paired changes are signed "
            "differences on those same triplets: per cent for input tokens (negative "
            "means fewer tokens), percentage points for rates, and absolute counts "
            "otherwise."
        ),
        "",
        "## Coverage",
        "",
        _render_coverage(summary["coverage"]),
        "",
        "## Per-task triplets",
        "",
    ]
    per_task = summary.get("per_task")
    tasks = per_task if isinstance(per_task, dict) else {}
    for task_id, payload in tasks.items():
        values = payload if isinstance(payload, dict) else {}
        lines.extend(
            [
                f"### {task_id}",
                "",
                f"Triplets: {values.get('triplets', 0)}.",
                "",
                _render_metric_table(values.get("metrics", {})),
                "",
            ]
        )
    excluded = summary.get("excluded_triplets")
    if isinstance(excluded, list) and excluded:
        lines.extend(["## Excluded triplets", ""])
        for item in excluded:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"- {item.get('task_id')} r{item.get('repeat')}: {item.get('reason')}"
            )
        lines.append("")
    lines.extend(_CAVEATS)
    return "\n".join(lines) + "\n"


def write_memory_report(
    records: list[dict[str, Any]],
    output_dir: Path,
) -> dict[str, Any]:
    """同时写出机器可读摘要和 Markdown 报告。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = summarize_memory_records(records)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "report.md").write_text(
        render_markdown(summary),
        encoding="utf-8",
    )
    return summary


_CAVEATS = (
    "## Reading this report",
    "",
    (
        "- The `relevant` arm consumes an expert-authored experience record. It "
        "measures the ceiling of consuming a correct record, not the quality of "
        "memory an agent would write for itself."
    ),
    (
        "- The `irrelevant` arm measures negative transfer: its stale record fires "
        "on the real anchor and points at a wrong fix location, so a run that "
        "trusts it edits the wrong file."
    ),
    (
        "- `hint_fired` only proves that a `memory` context block reached the "
        "provider request; it does not prove the agent followed it."
    ),
    (
        "- `stale_follow` only inspects the first tool call of an attempt, and "
        "`stale_hint_acted` only the first `write`/`edit` tool call, so late "
        "detours and shell-based edits are not counted."
    ),
    (
        "- `Steps to anchor (diagnostic)` is a weak signal in these fixtures: both "
        "`TASK.md` and the second turn name the file to modify, so every arm "
        "reaches the anchor at roughly the same step. It is reported as a sanity "
        "check on tool ordering, not as an outcome."
    ),
    (
        "- Token means include only triplets whose three attempts all reported "
        "complete provider usage; every other row keeps its own cohort."
    ),
    (
        "- Do not quote percentages before 20-30 tasks with multiple repeats and "
        "per-task pairs have been inspected (see `benchmarks/README.md`)."
    ),
    "",
)


def _render_metric_table(metrics: dict[str, Any]) -> str:
    lines = [
        (
            "| Metric | Cohort | none | relevant | irrelevant | relevant - none "
            "| irrelevant - none |"
        ),
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for label, field, value_kind in _METRICS:
        values = metrics.get(field)
        entry = values if isinstance(values, dict) else {}
        arms = entry.get("arms")
        arm_values = arms if isinstance(arms, dict) else {}
        lines.append(
            "| "
            + " | ".join(
                [
                    label,
                    _cohort(entry.get("cohort")),
                    _format_value(_arm_field(arm_values, "none", "mean"), value_kind),
                    _format_value(
                        _arm_field(arm_values, "relevant", "mean"), value_kind
                    ),
                    _format_value(
                        _arm_field(arm_values, "irrelevant", "mean"), value_kind
                    ),
                    _format_value(
                        _arm_field(arm_values, "relevant", "delta"), _delta_kind(field)
                    ),
                    _format_value(
                        _arm_field(arm_values, "irrelevant", "delta"),
                        _delta_kind(field),
                    ),
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def _render_coverage(coverage: dict[str, Any]) -> str:
    lines = [
        (
            "| Arm | Attempts | Complete usage | Hint fired | Steps to anchor "
            "measured | Steps to anchor null |"
        ),
        "|---|---:|---:|---:|---:|---:|",
    ]
    for arm in ARMS:
        values = coverage.get(arm)
        entry = values if isinstance(values, dict) else {}
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{arm}`",
                    str(entry.get("attempts", 0)),
                    str(entry.get("usage_complete", 0)),
                    str(entry.get("hint_fired", 0)),
                    str(entry.get("steps_to_anchor_measured", 0)),
                    str(entry.get("steps_to_anchor_null", 0)),
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def _metric_summary(triplets: list[_Triplet]) -> dict[str, Any]:
    return {
        field: _summarize_metric(triplets, field, value_kind)
        for _label, field, value_kind in _METRICS
    }


def _summarize_metric(
    triplets: list[_Triplet],
    field: str,
    value_kind: str,
) -> dict[str, Any]:
    eligible = [
        triplet
        for triplet in triplets
        if all(_available(triplet.records[arm], field, value_kind) for arm in ARMS)
    ]
    arms: dict[str, Any] = {}
    for arm in ARMS:
        values = [float(triplet.records[arm][field]) for triplet in eligible]
        arms[arm] = {
            "mean": fmean(values) if values else None,
            "observed": len(values),
            "total": sum(values),
        }
    arms["relevant"]["delta"] = _paired_delta(eligible, "relevant", field, value_kind)
    arms["irrelevant"]["delta"] = _paired_delta(
        eligible, "irrelevant", field, value_kind
    )
    return {
        "cohort": len(eligible),
        "value_kind": value_kind,
        "delta_kind": _delta_kind(field),
        "arms": arms,
    }


def _paired_delta(
    triplets: list[_Triplet],
    arm: str,
    field: str,
    value_kind: str,
) -> float | None:
    if not triplets:
        return None
    baseline = [float(triplet.records["none"][field]) for triplet in triplets]
    compared = [float(triplet.records[arm][field]) for triplet in triplets]
    if _delta_kind(field) == "percent":
        baseline_mean = fmean(baseline)
        if not baseline_mean:
            return None
        return (fmean(compared) - baseline_mean) / baseline_mean
    return fmean([value - base for value, base in zip(compared, baseline, strict=True)])


def _coverage(triplets: list[_Triplet]) -> dict[str, Any]:
    coverage: dict[str, Any] = {}
    for arm in ARMS:
        records = [triplet.records[arm] for triplet in triplets]
        steps = [record.get("steps_to_anchor") for record in records]
        coverage[arm] = {
            "attempts": len(records),
            "usage_complete": sum(
                bool(record.get("usage_complete")) for record in records
            ),
            "hint_fired": sum(bool(record.get("hint_fired")) for record in records),
            "steps_to_anchor_measured": sum(value is not None for value in steps),
            "steps_to_anchor_null": sum(value is None for value in steps),
        }
    return coverage


def _available(record: dict[str, Any], field: str, value_kind: str) -> bool:
    if field == "input_tokens_total" and not record.get("usage_complete"):
        return False
    value = record.get(field)
    if value is None:
        return False
    if value_kind == "rate":
        return isinstance(value, bool)
    return isinstance(value, int | float) and not isinstance(value, bool)


def _delta_kind(field: str) -> str:
    return _DELTA_KINDS.get(field, "count")


def _arm_field(arm_values: dict[str, Any], arm: str, key: str) -> object:
    values = arm_values.get(arm)
    entry = values if isinstance(values, dict) else {}
    return entry.get(key)


def _cohort(value: object) -> str:
    count = value if isinstance(value, int) and not isinstance(value, bool) else 0
    return f"n={count} triplets"


def _integer_field(record: dict[str, Any], field: str) -> int:
    value = record.get(field)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _format_value(value: object, kind: str) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "n/a"
    number = float(value)
    if kind == "percent":
        return f"{number * 100:+.1f}%"
    if kind == "points":
        return f"{number * 100:+.1f} pp"
    if kind == "count":
        return f"{number:+,.2f}"
    return f"{number:,.2f}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Regenerate a memory experience benchmark report."
    )
    parser.add_argument("results", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    records = load_memory_records(args.results)
    output_dir = (args.output_dir or args.results).resolve()
    write_memory_report(records, output_dir)
    print(output_dir / "report.md")


if __name__ == "__main__":
    main()
