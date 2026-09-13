from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read(name: str) -> str:
    return (ROOT / "scripts" / "automation" / name).read_text(encoding="utf-8")


def test_launcher_keeps_collector_and_report_boundaries_read_only():
    # scheduler経由で外部書込みやworkspace更新を許す回帰を防ぐ。
    launcher = read("run_pm_routine.ps1")
    assert "[string]$CommonDirectory" in launcher
    assert "[string]$CommonDirectory" in launcher
    assert "collect_pm_routine.ps1" in launcher
    assert "codex\\pm-routine\\reports" in launcher
    assert "state: running" in launcher
    assert "no external post, claim, or worker dispatch occurred" in launcher


def test_registration_is_daily_interactive_and_does_not_replace_by_default():
    # 無人実行が他のタスクを置換したり、非ログオン中に動く回帰を防ぐ。
    registration = read("register_pm_routine.ps1")
    assert 'taskPath = "\\Codex\\"' in registration
    assert 'taskFolderPath = "\\Codex"' in registration
    assert 'taskName = "AgentWorldPmRoutine"' in registration
    assert "New-ScheduledTaskTrigger -Daily -At 09:00" in registration
    assert "-LogonType Interactive" in registration
    assert "New-TimeSpan -Minutes 10" in registration
    assert "-MultipleInstances IgnoreNew" in registration
    assert "-not $Replace" in registration
    assert "runtimeLauncher" in registration
    assert "-CommonDirectory" in registration
    assert "-WorkingDirectory $runtimeDirectory" in registration
    assert "Copy-Item -LiteralPath $launcher -Destination $runtimeLauncher" in registration


def test_prompt_forbids_mutations_and_requires_escape_status():
    # 棚卸しがFront DeskのclaimやIssue更新を行う回帰を防ぐ。
    prompt = read("pm_routine_prompt.md")
    assert "Front Deskのclaim" in prompt
    assert "workerのdispatch・再起動" in prompt
    assert "#53 → #66 → #79 → #80" in prompt
    assert "GitHub readを最大8回" in prompt


def test_collector_bounds_reads_and_marks_missing_sources_unobserved():
    # 読取りが無期限に停止したり、未観測のPRやDashboardを成功扱いする回帰を防ぐ。
    collector = read("collect_pm_routine.ps1")
    assert "-TimeoutSec 15" in collector
    assert "'/issues?state=all&per_page=100'" in collector
    assert "closed_unmerged" in collector
    assert "dashboard: unobserved" in collector
