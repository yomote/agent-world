# Issue #89 入金消込Agent 着工・検証packet

状態: **実装中**。owner: `/root/pm_controller/logistics_implementation`。

- worktree: `C:/Users/omote/.codex/task-checkouts/agent-world-accounting-agent`
- branch: `codex/89-accounting-cash-application`
- base: `main@031288738cd2efe5d929060926ffd1470afd3133`
- Issue: [#89](https://github.com/yomote/agent-world/issues/89)
- 設計正本: [会計Agent提案](../proposals/accounting-cash-application-agent.md)、[ADR 0013](../adr/0013-read-only-accounting-agent.md)

## scopeと成果

公開known例は70,000円請求、50,000円入金、取消済み20,000円値引きの1請求である。最初のactual model runは、残高/取引先/請求書/調整履歴/候補solver/銀行明細/メールを観測順に選び、50,000円の配分案、20,000円の予定残高、台帳変更なし、営業への確認事項をpackageへ出した。

これは開発中のknown例である。単一請求への部分入金だけを扱い、合算・分割は未実装である。同額候補の未知bundleはvalidator上で任意配分を拒否するが、actual Agentが観測に応じて保留できるかは未評価である。固定workflowより優れることも未実証であり、Issue #89はこの縦切りだけでは完了にしない。

## actual modelの途中証跡

- 旧backend snapshot `f9113e1` 上のknown run: `run-c0bba0f5-7dbf-4e43-99d1-0f33206f4926`
- 8 decisionsで `ready_for_review`、artifact `package-0214b3fa3dff8994`
- その前の7-decision runは、調整履歴refを発行済み根拠として登録しなかったためdomain reject。契約を修正した。
- UTF-8 stdin診断失敗3回も保守的にattemptへ数え、初期24枠は18使用、残り6。blocking修正の再reviewまで追加callを停止する。
- この証跡は、後から見つかったdurability/evidence/baseline等のblocking修正前であり、最終current-head PASSではない。

## 実装境界

- `apps/world/accounting_*`: typed read-only snapshot、資料、候補solver、validator。World残高を変更しない。
- `apps/accounting_agent`: Codex structured decision adapter、bounded controller、SQLite run/receipt/evidence/artifact履歴、API。
- `apps/web/src/AccountingLab.tsx`: 前説明、実行状態、質問、review package、必要時だけtrace。
- known fixtureと未知M8欠落fixtureは分離。評価期待値は `apps/accounting_agent/tests/evaluator/` にあり、Agentへ渡さない。

## 検証の区別

backend snapshot `f9113e1` はtargeted 24 tests/ruffを通した後、独立reviewで7観点のblockingを受けた。修正snapshot `ca7cfeb` の限定reviewで残ったreceipt優先、tenant付き正本、current source照合、stateful/holdout束縛を修正した。続くsnapshot `3e6ed4d` ではatomic claim/CASとadjustment根拠の金額束縛を修正した。現在の候補は、typed evidence、receipt/CAS、旧SQLite履歴migration、再起動復元、run上限、同条件baseline、stateful PBT、同時claim、初回/replayの安定した公開failure codeを含むtargeted 40 testsと限定Ruffが通過している。独立再reviewは継続中である。

modelを使わない固定workflowは同じtyped fact、read tool、solver、validatorでknown例とM8欠落例を実行した。known例は50,000円配分・予定残高20,000円・台帳不変、M8は配分0円・未配分30,000円・営業照会・台帳不変となった。これはbaselineの動作確認であり、Agentとの優劣評価ではない。

UIはAgent/固定workflowの各latest runを並べ、判断回数、tool回数、配分、未配分、取消を含む観測済み調整と根拠を表示する。write失敗時は同じwriteを再送せず、既知runだけをGETで1回照合して結果不明、予算上限、domain failure、通信失敗を分ける。

current candidateの全体check、実browser、修正後actual known/unknown評価、独立再reviewは未完了である。旧head `90a7469` で開始した全体checkは後続backend変更の証跡に昇格させない。API契約checkとweb buildはcurrent dirty snapshotで通過したため、candidate固定後に再確認する。actual modelは旧phaseで18 attemptを使用済みとして引き継ぎ、最終phaseの追加上限22（cumulative最大40）案をhost側で強制する。再reviewとfreeze完了まで追加callを停止する。

Draft PR作成、push、mergeはこのpacket時点では未実施。本番deploy、実口座、帳簿確定、外部メール、本番認証、新provider/課金は行わない。
