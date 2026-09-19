# 0011. Windows Task Schedulerで日次PM棚卸しを起動する

- Status: Accepted
- Date: 2026-09-13

## 決定

公開repositoryを読むPM棚卸しcollectorは、worker runtime、Dashboard、CI証跡と分離したread-onlyの観測者として置く。Taskの登録、時刻、読取り予算、reportの状態分類は[日次PM棚卸し](../runbooks/pm-routine.md)を正本とする。

## 境界

worker runtime、Dashboard、CI証跡は未接続であり未観測と明記する。PM判断はreportを読むPMがIssue/PR正本と突合して行う。投稿、label更新、claim、worker dispatch、token保存、常駐daemon、再試行は行わない。
