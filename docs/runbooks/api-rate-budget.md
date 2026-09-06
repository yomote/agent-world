# 外部APIのレート予算

GitHubを含む外部APIは、公開されている上限を使い切る前提で設計しない。二次制限は公開値だけでは予測できず、同じ利用者・IP・token・workflowの呼出しが合算されることがある。応答性はGitHubへの巡回ではなく、イベントと集約済み状態で得る。

## 経路と所有者

| 経路                 | 起点                       | GitHub API                           | 状態の読み方                               |
| -------------------- | -------------------------- | ------------------------------------ | ------------------------------------------ |
| 開発CI               | `pull_request` / `push`    | workflow実行中に必要な最小限だけ     | Actionsのevent contextとcheckoutを優先する |
| merge gate           | 人がmainから一度dispatch   | 対象PR・current head・必要な保護だけ | 1実行に閉じた直列client                    |
| 状態画面・Front Desk | Codex状態変更または集約API | 直接取得しない                       | Codex / Azureのサニタイズ済みsnapshot      |
| GitHubとの照合       | event欠落時だけ            | 対象Issue・PR・最新runだけ           | activeは30分ごと、idleは60分ごと           |

状態画面のブラウザ、Codex agent state、Front DeskはGitHub APIを呼ばない。GitHub更新はWebhookやGitHub Actionsのeventから集約側へ反映する。Webhookが使えない初期段階では、上表の限定照合だけを1つの所有者が実行する。全open PR、全Issue、全run、全ログをscanしない。

## 実装規約

- event-drivenを第一候補にする。`schedule`、comment、label、CI完了を契機に別workflowやAPI監視を連鎖させない。
- API clientはサービス・資格情報・repositoryごとに直列の共有budgetを持つ。別プロセスへ広げるときは、共有永続ストアまたは単一のingest ownerを先に用意し、各ブラウザやagentが独自にpollしない。
- GETの再照合はETagを保存して`If-None-Match`を送り、`304 Not Modified`を成功として記録する。ページングは対象の上限を先に決め、上限を超えたら全件が確認できないとして停止する。
- retryは読み取りだけで、最初の要求の後に最大2回まで。書込みのtimeout、408、429、5xxなど結果不明は再送しない。knownな401、権限403、validation失敗も再送しない。
- 429またはrate-limitと判定できる403は、そのclientの新規要求を停止する。`Retry-After`と、残量0時の`X-RateLimit-Reset`のうち遅い時刻までdeferする。どちらもなければ60秒、次回は120秒で、読み取り再試行を使い切れば人へ状態を渡す。待機sleepでworkerを占有せず、deferred状態を保存して次のeventまたは予定時刻に再開する。
- 401、rate-limitではない403、認証拒否、アカウント拒否は即時停止する。別token・別accountへの自動fallbackはしない。
- 各ownerは`requests`、最後に成功した取得時刻、`defer_until`、最後のHTTP結果を、tokenやURL queryを含めず状態snapshotへ出す。失敗したjobの必要ログだけを一度保存し、同じログを再取得しない。

この予算値は安全な回数を保証しない。GitHubは一次・二次制限を変更でき、短時間の集中や高コストな要求にも制限をかける。実装と運用は公式の[REST API best practices](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api)、[rate limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api)、[conditional requests](https://docs.github.com/en/rest/using-the-rest-api/using-rest-api/conditional-requests)、[webhook best practices](https://docs.github.com/en/webhooks/using-webhooks/best-practices-for-using-webhooks)を正本とする。

## CI/CDでの適用

workflowは同じPR/branchの古い実行をconcurrencyでcancelし、対象外のworkflowを作らない。必須checkをPendingに残さないため、workflow全体のpath filterではなくjob条件を使う。CDの外部変更は、同一headに対して一度だけ行い、結果不明なら再送せず後続を止める。

merge gateのGitHub clientは1回のdispatch内で直列に実行し、CI待ちは60秒以上・最大10回で打ち切る。実行の最後にAPI使用数、最終成功、次回までのdefer時刻をjob logへ出す。これはその実行内の可視化であり、複数workflowにまたがる共有残量を推測する値ではない。

## 変更時の確認

新しい外部API client、Webhook consumer、scheduled reconciliation、CD providerを足すPRは次を明記する。

1. event経路とfallbackの対象・active/idle間隔。
2. shared budgetの保存場所とowner、ETag/304、ページング上限。
3. 429/403/401、timeout、unknown write時の停止状態と表示項目。
4. concurrency、同時実行、重複event、再実行の上限。
5. APIを使わないローカルtestと、実サービスで未検証の項目。
