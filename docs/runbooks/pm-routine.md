# 日次PM棚卸し

`\Codex\AgentWorldPmRoutine` は毎日09:00 JST、ログオン中だけ、最大10分で動く読取り専用Taskである。重複起動は `IgnoreNew`。公開GitHub RESTをtokenなしで最大8回読むため、秘密やCodex loginは使わない。

登録と最初の確認は次で行う。

```powershell
powershell -NoProfile -File scripts/automation/register_pm_routine.ps1 -Replace -RunNow
$common = git rev-parse --path-format=absolute --git-common-dir
Get-Content (Join-Path $common "codex/pm-routine/reports/latest.md")
```

reportは `completed`、一部観測できた `partial`、観測0件の `failed`、起動不能時の `not_run`、開始直後の `running` を区別する。`running` は開始記録であり、live稼働や完了の証明ではない。Issue/PR、worker runtime、Dashboard、証跡の未観測は成功扱いしない。reportを読むPMが正本のIssue/PRと突合して次手を判断し、collectorはclaim、投稿、label更新、worker dispatchを行わない。
