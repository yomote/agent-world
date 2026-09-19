# 開発・運用基盤の判断記録

GitHub、CI、開発automation、Front Deskのregistry、PM棚卸しは製品アプリのarchitectureではない。この文書は、これらの仕組みを採用した背景、構造上の制約、代替案、トレードオフを残す移転記録である。

現在の設定値・実装状態は各対象の仕様文書、実行手順・承認・予算・ownerはrunbookを正本とする。ここに操作手順や承認条件を重ねない。

| 旧ADR | 現行の技術・設定正本                                      | 運用正本                                                                                     |
| ----- | --------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| 0003  | `.github/workflows/ci.yml`                                | [CIと外部アクセスの運用](runbooks/ci.md)・[外部APIのレート予算](runbooks/api-rate-budget.md) |
| 0004  | [GitHub設定のIaC](../infra/github/README.md)              | 同READMEの導入・照合節                                                                       |
| 0005  | `scripts/merge_gate.py`・`scripts/trusted_local_merge.py` | [CIと外部アクセスの運用](runbooks/ci.md)                                                     |
| 0009  | `scripts/automation/runner.py`                            | [自律改善runner](runbooks/self-improvement.md)                                               |
| 0010  | `scripts/manage_status_registry.py`・status API schema    | [Front Desk依頼registry運用](runbooks/request-registry.md)                                   |
| 0011  | `scripts/automation/register_pm_routine.ps1`              | [日次PM棚卸し](runbooks/pm-routine.md)                                                       |

## 0003: CIの重複実行と開発エージェントの外部照会

以前のプロジェクトではCI/CDと開発エージェントの取得が重なって停止したとの報告があったが、原因の一次記録はない。このため、GitHubの標準concurrencyで同一PRまたはブランチの古い実行を止め、Draftのjobはskipし、コメント・定刻・CI完了からの連鎖起動を置かない方針を採った。

concurrencyはworkflow作成数や別PR間の総量を制限しない。Draft skipは検証成功ではなく、Ready前はローカル検証が必要である。GitHubの非公開かつ変動する制限を共通監視基盤だけで回避できるとは扱わない。

## 0004: GitHub設定を宣言から照合する

GitHub設定は`infra/github`のTerraformを正本とし、独自同期を重ねない。repository、main ruleset、Actions権限、依存脆弱性の通知と修正提案を対象にし、GitHubが直接読むworkflowとDependabot設定はYAMLのまま管理する。

設定適用には対象repo、公開範囲、管理API認証が必要である。ローカル検証の成功は実環境との一致を示さない。初期stateはローカルとし、共有運用へ移る前にアクセス制限・暗号化・lockを備えたbackendを選ぶ。独立reviewの自動起動、定期drift検知、二重着工防止、クラウド配備は前提が揃うまで追加しない。

## 0005: trusted sourceからcurrent headを統合する

reviewと検証をcurrent headへ結び付けても、保護されたmainへ統合する主体を分けなければ、PR由来コードを特権contextで実行したり、自己発行statusだけで統合したりする危険がある。gateはreview済みtrusted sourceから読み、PR由来workflowやworktreeを特権実行しない。current headだけをexpected SHA付きsquash mergeの対象とし、merge、通信失敗、認可拒否、条件未充足を区別する。

提案、承認、確定を一つの入力に混ぜず、formal local entryではidentity、repository、source tree、proposal artifact、approval evidence、operation receiptを分離する。deployはmergeとは別責務であり、entryはdeploy確定やpost-merge dispatchを担わない。

`pull_request_target`でPR由来コードを実行する案、native auto-merge、広い認証へのfallback、未merge sourceの自己bootstrapは採らない。前者は未reviewコードへ特権を渡し、middle二つは条件変化や可視性不足を安全と誤認し、最後はtrusted sourceを成立させないためである。証跡や認可が欠ければ止まることを、統合速度より追跡可能性のために選ぶ。同一GitHub accountのcommentは人間の独立性を証明しない。

## 0009: bounded local改善runner

改善候補の冪等な実行、試行予約、中断回復には、checkoutに固定したSQLiteとOS lockを使う。書込み前に試行を予約し、lockの解放とlease期限を確認した後だけ中断作業を`unknown`へ回収する。結果不明の書込みは再送しない。固定recipeとallowlistにより、任意promptやshellを実行せず変更量と検証条件を限定する。

SQLiteは予約・状態・履歴を一括確定しやすいが、DBとfileは単一transactionにできないため、クラッシュ後はhash照合で読み取り回復と結果不明を分ける。leaseだけでのowner乗取りを避けるためOS lockも使う。単一checkoutだけを対象にし、複数PCや悪意ある同一権限プロセスの協調は扱わない。一般的なコード改善や連続campaignは別の拡張であり、このrunnerはPM、Issue、他work itemを自動claimしない。

GitHub backendは既存GCMをprocess内で使う単一REST/GraphQL adapterに固定し、connector fallbackとworkflow dispatchを実行経路から除く。操作予約は再起動・helper開始でresetせず、review、current check、CI、PR/head、保護、threadsを結合する。

## 0010: Front Desk依頼registryと明示handover

Issueは目的・受入条件・課題状態、PRは差分・検証証跡の正本であり、`request_registry`は参照先・公開cache・担当・引継状態を結ぶ索引に限定する。更新はStatus.Ingest principalの専用PUTから受け、ETagとgenerationの双方を照合する。指定requestだけをmergeし、同一内容ではrevision、generation、`updated_at`を変えない。

handoverはprepareとclaimを分ける。prepareはactive Front Deskとworker状態を変えず、後継alias、generation、bundle digestをreadyとして保存する。claimはnamed successor、expected generation、digestの全一致時だけactive Front Deskを移す。owner移転generationと通常更新generationを分離し、raw runtime IDは公開しない。bundleはUTF-8・key辞書順・空白なしのJSON preimageをSHA-256で結び、receiptは今回のrequest、generation、opaque revisionだけを返す。

alias、thread ID、task pathはsecurity identityではない。既存Status.Ingest認証、ETag CAS、generation、成功receiptのexact照合で、二重claim、古い窓口の更新、旧current表示の復活を抑える。悪意あるpublisherの偽装やAPIによるroot真正性の証明は提供しない。worker再接続は明示dispatchであり、handover完了から自動再開しない。

## 0011: Windowsの日次PM棚卸し

公開repositoryを読むcollectorは、worker runtime、Dashboard、CI証跡と分離したread-only観測者にする。PMはreportをIssue/PR正本と突合して判断し、collectorは投稿、label更新、claim、worker dispatch、token保存、常駐daemon、再試行を行わない。未接続のruntimeや証跡は未観測として扱い、成功に補わない。
