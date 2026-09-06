# Mind Inboxの開発工場を取り込む

確認日: 2026-09-05。参照コードは [yomote/mind-inbox @ d3c1527](https://github.com/yomote/mind-inbox/tree/d3c15275b50d3686dba226fd98a1bdffc581a8dd)。初期の[移植計画](porting-plan.md)を、工場機能とGitHub設定のIaCも取り込むという今回の指示で拡張する。

## 棚卸しと採否

「実装がある」「動いた記録がある」「このプロジェクトで稼働している」は区別する。以下のMind Inboxの状態は参照コミットとIssueの記録に基づき、現在の全workflowの健全性を再測定したものではない。

| 機能                         | Mind Inboxで確認した状態・根拠                                                                                                                                                                                       | このプロジェクトでの扱い                                                                                                                              |
| ---------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| lint / format / unit / build | [test.yml](https://github.com/yomote/mind-inbox/blob/d3c15275b50d3686dba226fd98a1bdffc581a8dd/.github/workflows/test.yml)に実装                                                                                      | 既存コマンドをfactory runnerとCIに接続                                                                                                                |
| API契約の生成とずれ検出      | 同workflowでOpenAPI再生成とdiffを実行                                                                                                                                                                                | Pydantic → OpenAPI → TypeScriptを検査。欠落・未追跡ファイルも検出し、検査で上書きしない                                                               |
| 自動化の実行痕跡             | [status runbook](https://github.com/yomote/mind-inbox/blob/d3c15275b50d3686dba226fd98a1bdffc581a8dd/docs/runbooks/status-page.md)に実装・制約の記録                                                                  | ローカルJSON/Markdown/ログ、CIのjob summaryとartifactに縮小。失敗・timeout・未完了を成功と分ける                                                      |
| 文書リンクの既製ツール化     | [#393](https://github.com/yomote/mind-inbox/issues/393)でlycheeを提案。参照時のdebt-checkは自作検出器                                                                                                                | lycheeを導入。インライン・参照形式のローカルリンクを検査。外部URLの死活は対象外                                                                       |
| 依存更新とActionのSHA固定    | [.github/dependabot.yml](https://github.com/yomote/mind-inbox/blob/d3c15275b50d3686dba226fd98a1bdffc581a8dd/.github/dependabot.yml)に実装                                                                            | npm / pip / Actions / Terraformの更新設定を追加。脆弱性通知・security updatesの有効化はTerraformに含める                                              |
| GitHub設定のIaC              | [Terraform runbook](https://github.com/yomote/mind-inbox/blob/d3c15275b50d3686dba226fd98a1bdffc581a8dd/docs/runbooks/github-terraform.md): YAMLとTerraformが併存。CIはfmt/validateまで、実環境planの認証経路は未配線 | [infra/github](../infra/github/README.md)を設定の正本にする。repo・default branch・ruleset・Actions・依存脆弱性を宣言し、importとplan/apply手順を用意 |
| 規約と実装のずれ検出         | [#351](https://github.com/yomote/mind-inbox/issues/351)でworkflowとwatcherの照合などを提案                                                                                                                           | 最初の適用は「必須チェック名とCI job名の一致」。Terraform mock testで検査                                                                             |
| 独立レビュー / review-gate   | [docs/team.md](https://github.com/yomote/mind-inbox/blob/d3c15275b50d3686dba226fd98a1bdffc581a8dd/docs/team.md)に実装記録と自動judge起動の欠落                                                                       | GitHub rulesetでPR・未解決スレッド・機械検査を管理。独立レビューの自動起動は未導入。自己承認不能の単独開発で承認数1を必須にしない                     |
| 定期PM / レビューRoutine     | 同team.mdに未登録・呼び忘れ時の沈黙を記録                                                                                                                                                                            | 後続候補。起動経路・利用枠・実行痕跡・欠落検出を確保してから導入                                                                                      |
| 二重着工防止 (claim ref)     | 同team.mdでは設計のみ。[#349](https://github.com/yomote/mind-inbox/issues/349)に失効条件の問題                                                                                                                       | 後続候補。共有GitHubと並行担当が必要になった時、取得・heartbeat・解放・異常終了からの回収を一式で設計                                                 |
| 並行作業の隔離               | [#402](https://github.com/yomote/mind-inbox/issues/402)、[#456](https://github.com/yomote/mind-inbox/issues/456)に同一ブランチ競合・許可待ち沈黙の実害                                                               | 現在のセッション規約を使用。元リポジトリの全件委任・モデル指定・常設承認は移植しない                                                                  |
| 合成ユーザー / UX自律改善    | [#304](https://github.com/yomote/mind-inbox/issues/304)に構想と着手条件                                                                                                                                              | 後続候補。まずSandboxの操作仕様と再現可能なブラウザ検証を確立し、探索と評価・コスト上限を決める                                                       |
| Azure / deploy / コスト監視  | 環境再構築の宣言・runbookが存在                                                                                                                                                                                      | 配備先が未決定。クラウドを導入する時点でネットワーク、権限、予算、state保管を同じIaC方針で追加                                                        |

2026-09-06に、手作業の単一課題ループを[runbook](runbooks/single-task-loop.md)として採用した。Issueの目的・状態、PRのcurrent headに結び付くreviewと検証証跡、短い振返りを定型化する。Mind Inboxの専用Routine、bot、強制CI checker、auto-merge、claim/CAS/WIPと大きなjournalは後回しとし、必要な権限・異常時の回収・実行痕跡を別課題で設計する。

## 実行する

依存が揃った状態で、リポジトリ直下から実行する。

```text
npm run factory:check
npm run docs:check
npm run iac:check
```

- `factory:check`: API契約、lint、format、test、buildを全体180秒の予算で実行する。失敗しても予算内で後続検査を続ける。期限後の検査はnot_runで残し、失敗・未実行があれば終了コード1。結果は `artifacts/factory/report.md` と `report.json`、個別ログ。
- `docs:check`: lychee 0.24.2。PATH、`LYCHEE_BIN`、または `artifacts/tools/lychee/lychee.exe` を使用する。
- `iac:check`: Terraform 1.16.1。PATH、`TERRAFORM_BIN`、または `artifacts/tools/terraform/terraform.exe` を使用する。provider取得にはネットワークが必要だが、mock testはGitHubを変更しない。
- ツール不在や起動失敗はエラー。検査が途中で停止したレポートはrunning/not_runのままとなり、passにはならない。

CIは[ci.yml](../.github/workflows/ci.yml)の1 job・最大10分に統合する。Draft中はskipし、Ready for review後に実行する。concurrency・起動イベント・確認予算は[CI運用](runbooks/ci.md)を維持する。必須チェックをpaths条件で起動しない設計にはしない。

## 次に満たす条件

1. 適用先GitHubリポジトリ、公開範囲、mainの初期内容を確定する。
2. 初回CIのcheck jobが実際に完走してから、[GitHub IaC手順](../infra/github/README.md)でimport・planを確認し適用する。
3. 再planで差分なしを確認して初めて「設定が実環境と一致」と記録する。
4. 継続的な管理APIの認証と共有stateの保管先が決まったら、定期drift検知を追加する。

設定ファイルの追加だけではGitHub上の機能は稼働しない。CIの実行結果、適用結果、未導入項目を混同しない。

## 今回の検証 (2026-09-06)

- `npm run check`が成功。時間予算修正後の最終 `factory:check` でもAPI生成物の一致、lint、format、Vitest 6件、pytest 23件、buildが成功。既存の依存非推奨警告とPhaserのbundleサイズ警告は残る。
- 工場スクリプトのテストでは、失敗後も次の検査を実行すること、欠落ツールをerrorにすること、停止しないプロセスをtimeoutにすること、全体期限後の検査をnot_runにしてレポートを保存することを実プロセスで確認した。
- 独立レビューで見つかった「各検査の待ち時間が合算されCIの10分期限を超える」問題は、工場runner全体の180秒上限と回帰テストで修正し、レビュアーが解消を確認した。
- `terraform fmt -check -recursive` / `validate` / `test`: 成功。mock policy test 1件で設定とCIの整合性を確認。provider lockはWindows/Linux両方を生成。
- lychee: ローカルリンクのエラー0。外部URLへのリクエストはoffline設定で0件。対象は直下・docs・.github・infraのMarkdownで、依存providerの文書は除外する。外部URLの死活・文書内容の正しさ・リンクのfragmentはこの検査の対象外。
- GitHub Actions上の実行、実環境のTerraform plan/apply、適用後の差分なし確認、共有backendは未実施。リポジトリの接続先はこの検証時点で未設定。

この節のVitest 6件・pytest 23件は、この棚卸し時点の検証履歴である。[Vertical Sliceの検証記録](verification.md)のVitest 6件・pytest 22件はそれ以前の履歴であり、異なる時点の合計を現在の検証結果として扱わない。
