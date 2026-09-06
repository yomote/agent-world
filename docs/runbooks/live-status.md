# 開発状況ページ

[開発状況ページ](https://yomote.github.io/agent-world/status/)は、`Factory rollout` milestoneのIssue、open PR、直近Actionsをpublic GitHub REST APIから読み取り、スマートフォン向けに表示する。公開repoの公開情報だけを扱い、tokenやsecretを配信物へ含めない。

## 正本と表示

- 目的とDoDはIssue、状態は`status:*` label、担当laneは`owner:*` labelを正本とする。
- PRのDraft / Readyと、関連する最新Actionsを表示する。完了したIssueは既定で折り畳む。
- `skipped`、`failure`、`cancelled`、`timed_out`を`success`へ読み替えない。
- ownerとstatusはGitHubへの担当・報告状態であり、Codex内部threadの実livenessを表さない。
- blockerの詳細と次の証跡はIssue / PRを正本とし、最新コメントは利用者が詳細を開いたときだけ1件取得する。
- `owner: pm`はAI PMの管理状態、`needs-human`はユーザー本人の承認待ちとして別表示する。AI PM判断をhuman approvalとして表示しない。

基本取得はmilestone、対象Issue、open PR、直近Actionsの4 requestである。1分ごとにcache期限だけをlocal確認し、外部APIは5分に1回までの自動更新とする。非表示tabでは更新しない。手動更新にも5分のcooldownを適用する。403 / 429 / 通信失敗では前回値をstaleと明示し、前回値がなければerrorとして表示する。

## Pages source

初回はmerge gate復旧を待たず公開できるよう、review済み`codex/live-status` branchの`/docs`をGitHub Pages sourceにする。PRがmainへmergeされたらsourceを`main`の`/docs`へ切り替え、同じURLを維持する。Pages設定の変更前には既存siteがないことをAPIで確認し、既存siteを上書きしない。切替結果が不明な場合は再送せず、APIで現在のsourceを確認する。

## Runtime表示の次段階

Phase 1のこのページはGitHub上の報告状態を表示し、先に公開する。Codex App Serverには`thread/status/changed`、`thread/list`、`thread/loaded/list`があるため、Phase 2ではlocal runtimeからsanitized eventを本人認証済みAzure endpointへ変化時だけ送る余地がある。

送信候補はagent label、role、status、updated_at、関連Issue / PRに限定する。会話本文、reasoning、tool argument、local path、secret、model token usageは収集しない。Codex CLI 0.153.4が生成したexperimental JSON schemaでは、threadに`agentNickname`、`agentRole`、`parentThreadId`、`status`、`updatedAt`があり、status notificationは`threadId`と`status`を持つことをread-onlyで確認した。一方、Desktopと別App Server processがloaded setを共有するか、active writerとsubscriptionをどう一意にするか、安全なpush credentialをどう渡すかは未確認である。これらと認証境界・保持期間を決めるまでPhase 2を実装済みと扱わない。Git commitやIssue commentをruntime heartbeatに使わない。
