# 0011. Windows Task Schedulerで日次PM棚卸しを起動する

- Status: Accepted
- Date: 2026-09-13

## 決定

公開repository `yomote/agent-world` を匿名GitHub RESTで読むPowerShell collectorを、現在ユーザーの `\Codex\AgentWorldPmRoutine` として毎日09:00 JSTに起動する。TaskはGit common directory下の安定runtime pathを実行し、reportも `codex/pm-routine/reports/latest.md` に残す。

collectorは全Issue一覧、全PR一覧、重点6 Issueを最大8 read、15秒timeoutで取得する。最初のread失敗後は追加readしない。全観測失敗は `failed`、部分観測は `partial`、全観測は `completed` とし、Taskもnonzeroで失敗を示す。

## 境界

worker runtime、Dashboard、CI証跡は未接続であり未観測と明記する。PM判断はreportを読むPMがIssue/PR正本と突合して行う。投稿、label更新、claim、worker dispatch、token保存、常駐daemon、再試行は行わない。
