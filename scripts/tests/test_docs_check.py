from pathlib import Path

CHECK_TOOLS = (Path(__file__).parents[1] / "check_tools.py").read_text(encoding="utf-8")


def test_demo_markdown_is_included_in_link_check():
    """デモ固有READMEと設計のリンク切れがroot docs検査から漏れる回帰を防ぐ。"""
    docs_command = CHECK_TOOLS.split('if sys.argv[1:] == ["docs"]:', 1)[1].split(
        'elif sys.argv[1:] == ["iac"]:', 1
    )[0]

    assert '"demos/**/*.md"' in docs_command
