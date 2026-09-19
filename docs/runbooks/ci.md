# CIと外部アクセスの運用

CIはローカル検証を終えた変更を確認するために使う。Agentによる小刻みなpush、CI結果の頻繁な取得、失敗時の再実行が重なって過剰なアクセスにならないよう、実行条件と確認回数を制限する。GitHub API、Webhook、集約状態の経路と停止規約は[外部APIのレート予算](api-rate-budget.md)を正本とする。

## ワークフローで制御する範囲

| 操作・イベント                               | CIの動作                                                        |
| -------------------------------------------- | --------------------------------------------------------------- |
| PRのない作業ブランチへのpush                 | 起動しない                                                      |
| Draft PRの作成・更新・再open                 | workflowの記録はできるが、check jobはskipしてrunnerを起動しない |
| PRをReady for reviewに変更                   | checkを実行する                                                 |
| 通常PRの作成・更新・再open                   | checkを実行する                                                 |
| PRをDraftに戻す                              | 同じPRの古い実行をキャンセル対象にし、新しいcheck jobはskipする |
| main / masterへのpush                        | 統合後のcheckを実行する                                         |
| 同じPRまたは同じ対象ブランチの次の実行       | 古い実行・待機を取り消し、最新だけを残す                        |
| コメント・ラベル・レビュー投稿・CI完了・定刻 | 起動しない                                                      |
| mainを指定したmerge gateの手動dispatch       | current headと保護条件を再検査し、成立時だけsquash mergeする    |

設定は [ci.yml](../../.github/workflows/ci.yml)。1回につき1job、実行時間は最大10分。npm/pipのダウンロードキャッシュを利用する。別PRは別グループであり、リポジトリ全体の同時実行数や時間あたりの起動回数を制限する設定ではない。連続pushのたびにworkflow自体は作られるため、pushをまとめる運用も必要になる。

工場機能の追加後もこの実行予算を維持する。check job内でAPI契約・lint・format・test・build、lycheeのローカルリンク検査、Terraformのfmt/validate/mock testを行う。各検査の結果を集約し、失敗・未実行があれば最後のstepでjobを失敗させる。途中で失敗した場合もsummaryとartifactへ取得済みの結果を残す。GitHub設定の必須チェック名は `check`と`container-check`とし、[Terraform](../../infra/github/README.md)のテストで両workflowのjob名との一致を確認する。GitHubへのplan/applyはPRのCIからは行わない。

工場runnerは全検査を合わせて180秒までとし、残り時間を各コマンドの期限として渡す。使い切ったら後続をnot_runで記録して終了する。これにより複数のハングを順番に待ってレポート保存前にjob期限へ達することを防ぐ。依存の取得やrunner自体の停止でGitHub側の期限が切れた場合、artifact保存は保証できないため、GitHubのtimed_out/cancelledを結果として扱う。

lychee本体は固定バージョン・OS・CPUアーキテクチャをキーにGitHub Actionsのキャッシュで再利用する。キャッシュがない場合だけ公式Actionから取得し、リンク検査そのものは毎回実行する。取得は最大2分、検査は最大1分。対象は直下・docs・.github・infraのMarkdownに限り、providerの依存文書は除外する。offlineで外部URLへの要求は0件、並列上限2・再試行0。結果は `artifacts/factory/links.log` に保存する。キャッシュの利用可否は未検証であり、GitHub初回実行時にhit/missの双方を確認する。

PRの検証とmain / masterの検証は、マージ前後の異なる内容を確認するため両方残す。ドキュメントも整形チェックの対象なのでpath filterは設けない。workflow単位のスキップによって必須チェックがPendingのままになる運用も避ける。Draftのskipは検証成功を意味しない。Ready for review後の最新の結果を確認する。

## Merge gateとformal local entryの運用

この節をmerge手順・承認条件・停止条件の正本とする。trusted source、current head、確定主体を分離する理由と代案は[開発・運用基盤の判断記録](../development-operations.md#0005-trusted-sourceからcurrent-headを統合する)に記録する。アプリ内のローカルWorld観測とは別の規約であり、Worldの1秒pollingやActionの動作は変更しない。

### main workflowのmerge gate

- 入口は信頼済み`main`の`workflow_dispatch`だけである。PR番号、40桁のexpected head、merge実行の真偽を明示入力し、`ref=main`以外ではjobを実行しない。コメント、label、CI完了、scheduleから新しいworkflowを連鎖起動せず、全open PRを巡回しない。
- gateはopenかつReady、base=`main`、同一repo、merge可能、対象外labelなしを検査する。`needs-human`と`release`は対象外とし、merge可否が未知の間も実行しない。独立review markerは対象head、`verdict: pass`、reviewer識別子を含むcurrent-head証跡である。同じGitHub accountからのmarkerはreviewerのidentityや独立性を認証しないため、PMが別会話で割り当てた独立reviewの受入記録として扱う。取り消すときは新しいtrusted declarationを`verdict: fail`で投稿し、gateは最新の宣言を優先する。
- gateは対象headの`pull_request` CI runの`completed/success`、activeなmain rulesetのPR必須・未解決thread禁止・strictな`check`必須・管理者を含むbypassなしをAPIで照合する。`skipped`、古いhead、別PR、自己発行commit statusは根拠にしない。権限不足、取得不能、100件を超えて全量を確認できないcomment/thread、head/baseのraceは停止条件である。
- 条件が揃ったときだけ、expected SHA付きsquash merge APIを1回呼ぶ。結果がunknownなら再送せず、同じheadのmerge完了をread-onlyで1回だけ確認する。CI待ちは60秒以上の間隔・最大10回、GitHub clientは直列とし、使用数・最終成功・defer時刻だけをjob logへ残す。
- `GITHUB_TOKEN`によるmerge pushはpush workflowを起動しないため、merge成功後に同じgateがmain CIを`workflow_dispatch`し、配備側へ`agent-world-merged` repository dispatchを1回送る。いずれかのdispatchがfailedまたはunknownなら再送せず、merge済みであることと各dispatchの`sent`、`failed`、`unknown`、`not_run`を記録して停止する。

### formal local entry

formal local entryの構造は採用済みだが、採用は個別operationの承認や実行成功を意味しない。通常のmain workflow入口を緩和しない追加の実行入口として、既定はdry-runにする。`--execute`、有効なapproval packet、rootの明示承認がそろわなければ実mergeしない。新credentialや権限を追加せず、親の`GH_TOKEN` / `GITHUB_TOKEN`を除去した指定ownerのstored `gh` authだけを使う。normal modeによる実merge成功は未記録であり、個別実行の成否はoperation receiptとPRの記録で判断する。この節の記述やpreflightだけで成功済みと扱わない。

- approval packetは対象PR、40桁expected head、trusted sourceとsource tree、指定owner、実行mode、expiry、対象headへ結び付く独立review・CI・root approval commentを含む。実行直前にAPIで照合する。root approval commentは同一GitHub accountの人間による独立承認を技術的に証明せず、trusted operator assertionの記録に限る。
- remote main SHAとclean detached sourceをtrusted sourceへ完全一致させる。strict gateは検証済みgit objectから一時snapshotへ読み、worktreeの変更後ファイルを実行しない。authority、repository、PR、head、source、modeから導出する固定pathへ、まず`not_attempted` phaseのoperation receiptを排他的に永続化する。merge PUTの直前には`put_started` phaseをfsyncし、保存できなければPUTを送らない。`put_started`後の通信・GateErrorはunknownのまま停止し、PUT前の決定的GateErrorだけを`failed` / `not_attempted`として記録する。旧receiptを補完せず、`in_progress`、`unknown`、`failed`、`merged`の既存receiptはすべてterminalであり、packet IDやcaller指定pathを変えても再起動後に自動再送しない。expiryは受付時とstrict gate内部のmerge PUT直前に確認する。
- ownerのmerge pushがdeploy workflowを起動し得るため、対象PR headの`.github/workflows` treeがtrusted sourceと完全一致し、`deploy-azure.yml` blobが監査済みdigestと一致することを確認する。既知版で`AZURE_DEPLOY_ENABLED`のfalse時guardがcheckout、image build、OIDC、Azureより前にあることを検査し、実行直前に完全なrepository variables一覧から同変数を読む。完全な一覧で不在、または明示`false`なら既知guardが副作用前に停止する。listingが不完全・取得不能、値が曖昧・`true`、workflow tree/blob/guard/environment/API読取りが未知または変更なら停止する。production environment保護は存在しても追加防御として許容する。管理者がpreflightからmerge APIまで設定を同時変更しないことをtrusted-operation前提として記録し、動的variableだけを安全保証とは扱わない。
- local entryはpost-merge dispatchを行わない。root承認なしに実merge、credential追加、post-merge dispatchを行わない。書き込み結果がunknownなら再送しない。

### bootstrapの境界

mainに存在しないsourceをformal local entryのtrusted sourceとして自己導入しない。未merge sourceを使うbootstrapは未承認であり、このrunbookは実行可能な代替手順を定めない。必要になった場合は、review済みcommit/tree hash、trusted launcher、rootの明示承認を含む別設計・別approval packetを先にレビューする。

## 開発エージェントの確認予算

以下は [AGENTS.md](../../AGENTS.md) で開発エージェントに適用する運用上限。GitHub側で強制するAPIレート制限や、実装済みの監視プログラムではない。画面やagent stateからGitHubを読ませず、eventの欠落を補う照合だけをactive 30分、idle 60分の頻度で1 ownerが行う。ETag、共有budget、停止時の表示は[外部APIのレート予算](api-rate-budget.md)に従う。

1. 編集中はローカルで確認し、意味のある変更単位でpushする。作業途中のPRはDraftを使う。
2. CIの完了通知やChecks画面を優先する。自動照会が必要なときは対象PR/runだけを直列に確認する。短い間隔のwatchコマンドは使わない。
3. 状態確認は60秒以上空け、1つのrunにつき最大10回。再試行もこの回数に含める。上限に達したら監視を終了し、対象runと「未完了・未検証」を報告する。再開して回数をリセットしない。
4. 完了したら照会を止める。失敗したjobだけ必要なログを一度取得し、手元で再利用する。成功したrunの全ログや、全PR・全コメントを繰り返し取得しない。
5. テスト失敗は原因を修正してローカルで確認する。CIの自動再実行ループを作らない。一時的な基盤障害と確認できた場合のみ、同じ内容の再実行は1回までとする。

60秒・10回という値は、この小規模プロジェクトの開始時の運用予算であり、GitHubが保証する「ブロックされない閾値」ではない。キュー待ちが長い場合はCIをキャンセルせず監視だけ終了する。

## 制限応答と通信失敗

- 429、またはレート制限と明示された403では、そのサービスへの追加要求を休止する。`Retry-After` と、`X-RateLimit-Remaining: 0` のときの `X-RateLimit-Reset` を確認し、指定された待機時間より早く再開しない。両方あれば遅い方まで待つ。
- 待機時間の指定がないレート制限は、最初は60秒、次は120秒以上待つ。読み取りの再試行は初回の後に最大2回で終了し、制限が続けばその時点で報告する。ヘッダーが見えないツールで制限が出た場合も同じ予算を使う。
- 認証・権限・アカウント拒否、レート制限と判断できない403は再試行しない。別アカウントやトークンへ切り替えて続行しない。
- 結果不明の書き込みは自動再送しない。取得できなかったCI結果をPASSと扱わない。

GitHubは短時間の集中アクセスに対する二次制限を設けており、公開されていない条件や変更される条件もある。上限内に収めてもブロック回避を保証するものではない。基準は[外部APIのレート予算](api-rate-budget.md)に集約する。

将来、外部取得やCDが必要になった時点で、対象サービスに合わせた共有キュー、キャッシュ、有界な再試行を設計する。今の段階で常駐監視や取得用の仕組みは増やさない。

## 変更時の確認

ローカルでは `npm run check` でYAML/Markdownの整形と既存の品質チェックを行う。これはGitHub Actionsのイベント処理を実行した証拠にはならない。

GitHubへの反映後、通常の開発で該当するイベントが発生したときに、上の表と実行結果を照合する。検証だけの連続pushや大量の試験PRは作らない。同一PRのキャンセル、別PRの独立性、Draftのskip、Ready for review後の実行が未確認なら、そのまま未検証と記録する。
