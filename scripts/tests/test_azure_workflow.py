from pathlib import Path

WORKFLOW = (Path(__file__).parents[2] / ".github" / "workflows" / "deploy-azure.yml").read_text(
    encoding="utf-8"
)


def test_container_check_is_created_for_every_pull_request():
    """無関係なPRでも必須checkが欠落して永久にmerge不能になる回帰を防ぐ。"""
    pull_request_trigger = WORKFLOW.split("  pull_request:", 1)[1].split("  push:", 1)[0]

    assert "paths:" not in pull_request_trigger
    assert "Detect container input changes" in WORKFLOW
    assert "if: steps.changes.outputs.container == 'true'" in WORKFLOW
    assert "if: steps.changes.outputs.container == 'false'" in WORKFLOW
