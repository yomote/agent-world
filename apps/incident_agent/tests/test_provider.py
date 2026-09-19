from pathlib import Path

from incident_agent.provider import CodexExecProvider


def test_codex_process_has_no_shell_file_or_environment_capability(tmp_path):
    """system promptだけに依存し、モデルがfixtureや真因manifestを直読できる回帰を防ぐ。"""
    command = CodexExecProvider.command(tmp_path, Path("schema.json"), Path("output.json"))
    joined = " ".join(command)
    assert "--sandbox read-only" in joined
    assert "--ephemeral" in command
    assert "--ignore-user-config" in command
    assert "--ignore-rules" in command
    assert "shell_environment_policy.inherit=none" in command
    for capability in (
        "shell_tool",
        "browser_use",
        "computer_use",
        "apps",
        "image_generation",
        "multi_agent",
    ):
        assert capability in command
