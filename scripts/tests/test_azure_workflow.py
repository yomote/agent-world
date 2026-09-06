from pathlib import Path

WORKFLOW = (Path(__file__).parents[2] / ".github" / "workflows" / "deploy-azure.yml").read_text(
    encoding="utf-8"
)


def test_container_check_is_created_for_every_pull_request():
    """無関係なPRでも必須checkが欠落して永久にmerge不能になる回帰を防ぐ。"""
    pull_request_trigger = WORKFLOW.split("  pull_request:", 1)[1].split("  push:", 1)[0]

    assert "paths:" not in pull_request_trigger
    assert "edited" in pull_request_trigger
    assert "Detect container input changes" in WORKFLOW
    assert "if: steps.changes.outputs.container == 'true'" in WORKFLOW
    assert "if: steps.changes.outputs.container == 'false'" in WORKFLOW


def test_container_comparison_errors_fail_the_required_check():
    """git diffの比較失敗を変更ありへ丸め、build成功で隠す回帰を防ぐ。"""
    detection_step = WORKFLOW.split("      - name: Detect container input changes", 1)[1].split(
        "      - name: Build and exercise production container", 1
    )[0]

    assert "diff_status=$?" in detection_step
    assert '0) echo "container=false"' in detection_step
    assert '1) echo "container=true"' in detection_step
    assert 'exit "${diff_status}"' in detection_step
