# Agent向け開発規約

このプロジェクトはResidentのActionとWorldの変化を観察するSandbox。まず [README](README.md)、[設計](docs/architecture.md)、[移植判断](docs/porting-plan.md) を読む。

## 不変条件

- World Simulator is authoritative。`apps/world/simulator.py` のみが内部WorldStateを更新する。
- UIとActorは観測とActionの発行だけ。Phaserで移動を計算・先行確定しない。
- `apps/world` はActor、React、Phaser、LLM SDKに依存しない。Actorは独立した提案モジュールに置く。
- 初期scopeはAとmoveのみ。文明・経済・戦闘・God Agent・複数Agent・LLMを要求なしに足さない。
- ドキュメントと説明は日本語、識別子は英語。
- 通信失敗・未検証・Worldのfailureを区別する。未実行のチェックをPASSと書かない。

## 開発ループ

1. 課題ごとの着工packet、状態、独立レビュー、current headへの証跡、短い振返りは[単一課題の完遂ループ](docs/runbooks/single-task-loop.md)に従う。正本は目的・状態がIssue、差分・証跡がPRであり、DraftのCI skipやdirty stateの結果をPASSとしない。
2. 変更対象の仕様と影響範囲を確認する。設計判断は `docs/adr/` に理由とトレードオフを記録する。
3. 必要最小限の変更を行い、静かに壊れる性質をテストする。各テストには防ぐ回帰をコメントで説明する。
4. `npm run check` でlint・format・unit・buildを実行する。単体だけで画面の動作確認済みとしない。
5. API変更時は `npm run api:generate`。`docs/api/openapi.json` と `apps/web/src/api/schema.d.ts` は生成物で手編集しない。
6. 動作・起動方法・制約が変わったら関連ドキュメントを同時に更新する。

## 開発のPMとワーカー

- 単一のユーザー窓口は低遅延のFront Desk、方針・優先順位・依存・担当の管理はPM controllerが担う。役割はモデル名に固定しない。両者の調査、編集、検証、レビュー、統合、commit、push、PR作成の禁止を維持し、実務は独立したwork itemとしてworkerへ委任する。
- task-primary sessionのownerは実装workerであり、この実務禁止を継承しない。実装、固定commitへの最大1名のread-only child reviewer依頼、修正・再検証・同じreviewerへの再依頼、許可済みのpush・Draft PR納品まで所有する。責務とpacketは [PMワークフロー](docs/runbooks/pm-workflow.md) を参照する。
- 並行セッションの担当範囲を確認し、同じファイルを無調整で編集しない。実行中のタスクの担当は合意なく変更しない。
- work item、top-level session、child agentと、指示・報告とspawnの違いは [Codexチーム運用](docs/runbooks/codex-team.md) に従う。委任のためだけに細分化せず、CIと外部アクセスの予算は全担当が守る。

## CIと外部アクセスの予算

- 編集・検証はローカルで進め、pushは意味のある変更単位にまとめる。編集中のPRはDraftを基本とし、CIを動かすためだけの空commit・連続push・自動再実行ループを作らない。
- CI確認はGitHubの完了通知やChecks画面を優先する。照会が必要なら対象PR/runに絞り、60秒以上の間隔で1つのrunにつき最大10回まで。上限に達したら監視を終了し「未完了・未検証」と報告する。同じrunの監視を再開して上限をリセットしない。
- ログは失敗したjobの必要な部分を一度取得して再利用する。全run・全ログ・全コメントの繰り返し取得や、複数の監視ループを並列に回さない。
- 外部APIの制限応答では処理を休止する。`Retry-After` と、残量0の場合の `X-RateLimit-Reset` を守り、両方あれば遅い方まで待つ。待機指定のないレート制限は60秒から待機を倍増し、読み取りの再試行は初回の後に最大2回で打ち切る。制限と判別できない403・認証拒否は再試行せず報告する。
- 書き込みの結果が不明な場合は自動再送しない。コメント・ラベル・CI完了から別の自動処理を連鎖起動する仕組みは追加しない。具体的な実行条件と上限は [CI運用](docs/runbooks/ci.md) を参照する。
- GitHubの公開上限を設計値にせず、イベント優先・共有budget・ETag・停止時の可視化は[外部APIのレート予算](docs/runbooks/api-rate-budget.md)に従う。

細かい開発手順は [ローカル開発](docs/runbooks/local-dev.md)、テストの責務は [テスト方針](docs/testing.md)。秘密・本番認証・運用自動化を持ち込まない。作業ブランチは `codex/` を使用する。
