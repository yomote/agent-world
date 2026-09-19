"""正規化済みsnapshotを検証し、local status storeへ原子的に保存する。"""

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=("pm-confirmed", "fixture"), required=True)
    parser.add_argument("--observed-at", required=True)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--pm-task-projection", type=Path)
    parser.add_argument("--pm-task-comment-output", type=Path)
    parser.add_argument("--output", type=Path, default=Path("artifacts/status/current.json"))
    return parser.parse_args()


def render_pm_task_comment(projection) -> str:
    """typed projectionと同じrowから正本参照用のbounded Markdownを作る。"""

    def cell(value) -> str:
        return str(value or "—").replace("|", "\\|").replace("\n", " ")

    lines = [
        f"### PM task観測（{projection.observed_at.isoformat()}）",
        "",
        (
            "PM確認済みpacketをworkerが代筆したread-only観測です。Issue/PRが正本であり、"
            "このcommentとboard projectionは再生成可能なcacheです。Front Desk claim、"
            "control registry、PO acceptance、GitHub live同期を示しません。"
        ),
        "",
        f"- source version: `{projection.source_version}`",
        f"- task content digest: `{projection.content_digest}`",
        "- source refs: " + ", ".join(str(ref) for ref in projection.source_refs),
        "",
        (
            "| task | owner | state | current step | next action | "
            "blocker / waiting / resume | Issue / PR | row source |"
        ),
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for task in projection.tasks:
        waiting = "; ".join(
            value
            for value in (
                task.blocker,
                (
                    f"{task.waiting_on}: {task.waiting_detail}"
                    if task.waiting_on not in {None, "none"}
                    else task.waiting_on
                ),
                task.resume_trigger,
            )
            if value
        )
        refs = f"{task.issue_url}" + (f" / {task.pr_url}" if task.pr_url else "")
        lines.append(
            "| "
            + " | ".join(
                cell(value)
                for value in (
                    task.task_id,
                    task.owner,
                    task.state,
                    task.current_step,
                    task.next_action,
                    waiting,
                    refs,
                    f"{task.source_version} @ {task.observed_at.isoformat()}",
                )
            )
            + " |"
        )
    lines.extend(
        [
            "",
            (
                "taskごとの観測clockでfresh/staleを判定し、projection全体の更新時刻で"
                "古いrowをfreshへ変えません。local server停止中は画面を開けず、"
                "native pushと24時間可用性は未実装です。"
            ),
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps"))
    from ops_status.models import (
        PmTaskProjection,
        StatusSnapshot,
        validate_pm_task_projection_transition,
    )

    args = parse_args()
    if args.input:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
    else:
        payload = json.load(sys.stdin)
    received_at = datetime.now(UTC)
    projection = None
    if args.pm_task_projection:
        projection = PmTaskProjection.model_validate_json(args.pm_task_projection.read_bytes())
        if not projection.source_refs:
            raise ValueError("PM task projection needs an authoritative source reference")
        if args.output.exists():
            previous = StatusSnapshot.model_validate_json(args.output.read_bytes())
            if previous.request_registry is not None:
                raise ValueError("PM task projection cannot overwrite an active request registry")
            if previous.pm_task_projection is not None:
                previous_projection = previous.pm_task_projection
                validate_pm_task_projection_transition(previous_projection, projection)
                if projection.observed_at < previous_projection.observed_at:
                    raise ValueError("PM task projection observation cannot move backwards")
                if (
                    projection.observed_at == previous_projection.observed_at
                    and projection != previous_projection
                ):
                    raise ValueError("PM task projection cannot change at the same observation")
        if args.pm_task_comment_output:
            args.pm_task_comment_output.parent.mkdir(parents=True, exist_ok=True)
            args.pm_task_comment_output.write_text(
                render_pm_task_comment(projection), encoding="utf-8"
            )
    elif args.pm_task_comment_output:
        raise ValueError("PM task comment output needs a typed projection")
    snapshot = StatusSnapshot.model_validate(
        {
            "schema_version": 1,
            "source": args.source,
            "observed_at": args.observed_at,
            "received_at": received_at,
            "items": payload,
            "pm_task_projection": projection,
        }
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(f"{args.output.suffix}.{os.getpid()}.tmp")
    temporary.write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(args.output)
    print(f"status snapshot written: source={snapshot.source} items={len(snapshot.items)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
