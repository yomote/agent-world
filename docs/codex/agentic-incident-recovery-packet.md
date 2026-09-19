# 障害復旧Agent Lab 着工packet

## 状態

- owner: `logistics_implementation` worker（既存担当から独立したwork item）
- branch: `codex/agentic-incident-recovery`
- worktree: `C:/Users/omote/.codex/task-checkouts/agent-world-incident-recovery`
- base: `origin/main@232838c801ba5cd2805c784978acfda1a759d298`
- GitHub: push / Issue / PR は未承認のため行わない
- 設計: Proposed。物流PR #88と元repoのdirty stateを変更しない

## 目的と受入

同じ「発送指示が10分停止」という症状に対し、実modelが観測結果から次tool、追加質問、proposal、停止を選ぶ。固定routingや録画replayをAgent成功と呼ばない。3原因と3 holdoutを決定的Simulatorで動かし、少なくとも異なる2故障の実model traceが異なるtool・差分になることを確認する。

Worldだけが状態を確定し、未承認・stale・hash不一致・action ID衝突では状態を変えない。結果不明は再送せずaction statusを照合する。承認済み変更後の実ledgerと一意canary 2件から漏れ・重複を集計する。

## 実装境界

1. `apps/world`: 6 scenarioの隔離service state、typed tool、proposal検証、承認、冪等write、bounded verify。真因manifestは公開model/UIへ投影しない。
2. `apps/incident_agent`: Codex CLI adapter、判断loop、予算、HITL、artifact。AgentはWorld HTTPしか操作しない。
3. `apps/web`: Actual model / Simulated services、状態、実数、公開理由、tool/evidence、質問、承認差分、結果を1画面で追う。
4. baseline: 同じtool・ACL・18 tool上限で動く決定的runbook。隠しmanifestは参照しない。

## model接続と予算

2026-09-17、`codex-cli 0.154.0-alpha.6.2` と既存ChatGPTログインで構造化出力の疎通を1回確認した。CLIはmodelの公開IDを出力しなかったため、識別子は **Codex CLI default (ID unreported)** とし、推測しない。API key、新しいAPI契約、プラン外課金は使わない。

1 run: model decision 8、tool 18、clarification 2、proposal 2、wall 180秒。process全体の非対話model callは16回で止める。rate limit、timeout、応答schema不正はunknownで停止し、自動再送しない。疎通前のschema不備によるHTTP 400は推論結果なし、修正後callはinput 11,372 / output 45 tokenだった。

## 評価計画

- deterministic harness: 3原因＋3 holdoutのWorld遷移、認可・stale・冪等・canary・artifact整合を全件検証する。
- actual model: まず購読ずれ、次に別種類の故障でmodel→tool→modelの複数loopと異なる差分を確認する。残りfixtureはcall予算と結果を明記し、未実行をPASSにしない。
- holdout: prompt/baselineのdigestを固定してから番号4〜6を開封する。結果を見て調整したfixtureはholdout結果から外す。
- UI: 状態、実数、質問と実行承認の分離、diff、回復artifact、unknown表示をブラウザで確認する。
- `npm run api:generate`、targeted test、`npm run check`、独立review、current head証跡を残す。

## 現在の既知制約

ローカルmemoryのみで再起動時に履歴は消える。本番認証・本番service・外部監視・任意shell・複数Agentはない。人間承認はローカルUIのoperator入力であり、本人性を認証しない。Codex CLI defaultの正確なmodel IDは未確認。実model E2Eとブラウザ確認が完了するまで「実装途中 / Agent実行未検証」である。
