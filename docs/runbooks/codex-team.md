# Codex チーム運用

この文書は、Front Desk、PM controller、work itemを完遂するtask-primary sessionとchild agentの関係、起動・同期・証跡の扱いを定めます。役割は責務で定義し、モデル名に固定しません。ゲーム内のAgent / World設計とは無関係です。

## 作業単位と実行単位

| 用語                  | 意味と識別                                                  | 所有・関係                                                     |
| --------------------- | ----------------------------------------------------------- | -------------------------------------------------------------- |
| work item / work unit | 目的、DoD、担当範囲を持つ作業。Issueやpacketの作業名で識別  | 実装・review・検証・納品を含む成果の単位                       |
| top-level session     | 独立して起動・再開するsession。session UUIDで識別           | task-primaryはこのsessionのownerとして1つのwork itemを完遂する |
| child agent           | 親sessionがspawnしたagent。子UUID、親UUID、taskの証跡で識別 | 例: task-primaryが依頼する正式なread-only reviewer             |

work itemの数、top-level sessionの数、child agentの数は別に数えます。PM controllerがpacketを渡した相手でも、独立CLIとして起動したprimaryはPMのchildとは限りません。`/root/...` のtask名やUI名だけで別sessionの親子関係を断定せず、UUIDと起動・spawnの証跡を照合します。指示・報告とspawnを分けた図は[PMワークフロー](pm-workflow.md#指示と報告の関係)にあります。

## 役割と設定の配置

| 役割                       | 責務                                                                                    |
| -------------------------- | --------------------------------------------------------------------------------------- |
| Front Desk                 | 単一のユーザー窓口。低遅延の受付、既知状態、既決事項、結果の集約                        |
| PM controller              | 方針、portfolio、優先順位、依存、owner、DoD、リスク、decision packet、状態遷移          |
| task-primary owner         | 担当work itemの調査・実装、review依頼と修正、検証、commit、許可済みのpush・Draft PR納品 |
| implementer / terra_worker | 複雑な実装 / 範囲の明確な通常実装に使う既存の役割定義                                   |
| scout                      | 限定した検索、調査、抽出をread-onlyで行う役割定義                                       |
| reviewer                   | 固定SHAの変更を独立に確認するread-onlyの役割定義。実装会話を継承しない                  |

Front Deskはtool待機やworker追跡・調整を持たず、PM controllerが管理の節目に差分snapshotを返します。Front DeskとPM controllerの調査・編集・検証・レビュー・統合・Git操作の禁止を維持します。task-primary ownerは実装workerであり、PMの実務禁止を継承しません。ユーザーが個別workerを巡回して管理する運用にはしません。分配、完遂ループ、承認、Remote継続は[PMワークフロー](pm-workflow.md)を正典とします。

設定の正本は [`.codex/config.toml`](../../.codex/config.toml) と `.codex/agents/` の4つの役割定義です。上表の責務をすべて設定ファイル名や新しいdispatcherへ対応させる必要はありません。本改訂はdefault model、並行数、役割定義のmodel・推論強度を変更しません。既存設定のモデル名は起動時の選択であり、役割の要件ではありません。risk、latency、costと利用可能なruntimeに応じて選び、秒数、金額、高速化率は未測定のため保証しません。

設定は起動済みsessionへ遡及適用されるとは限りません。起動時に有効な設定と利用可能な引数を確認します。既存の `implementer` 定義は推論強度を固定していないため依頼時に明示します。他の役割定義に設定された値が呼出し側の指定だけで変わるとは扱いません。モデル変更と全履歴継承を併用できないruntimeでは、`fork_turns = "none"` または必要な直近履歴だけを指定し、独立したpacketを渡します。設定形式は[OpenAI公式のSubagents資料](https://learn.chatgpt.com/docs/agent-configuration/subagents)を参照します。

## 分配前

1. PM controllerは既知の担当・依存に基づいて独立したwork itemを割り当て、ownerが `AGENTS.md`、既存owner、関連PRと作業場所を確認します。
2. 目的、DoD、owner UUID、worktree、branch、base、編集境界、依存、検証、報告形式、許可済み操作を[packet](pm-workflow.md#分配packetと着工)にまとめます。正式なchild reviewerの依頼権限も同じpacketへ明記し、通常の修正・再reviewをPMへ戻しません。
3. 作業中の同じファイルは触りません。重なる場合はownerとPM controllerが移管に合意し、独立worktreeなどで隔離してから着手します。稼働中workerの担当は本人の修正完了まで維持します。

共有ディレクトリは自動で worktree に隔離されません。無関係なファイルを restore、stash、commit しません。Git コミットや外部投稿は、そのタスクの権限と担当者間の同期後に行います。

## 正式なchild review

task-primary ownerが実装と検証後にcommitを固定し、最大1名の正式なread-only child reviewerを `fork_turns = "none"` で依頼します。PM controllerではなく、そのwork itemのownerが依頼元です。reviewerには次を渡します。

- work itemの目的・DoDと対象worktree、baseと固定40桁SHA、担当ファイル、禁止範囲。
- 実施した検証と結果、未検証事項。作者の結論をreview結果として引き継がせない。
- read-only、編集・Git書き込み・外部投稿・再委任なし、指摘の重要度・箇所・根拠と対象SHAを返すこと。

ownerは指摘を修正して再検証し、内容が変われば新SHAで同じreviewerへ再reviewを依頼します。新しいreviewerを追加して上限を回避しません。レビュー枠や担当が利用できなければ `review_pending` または阻害要因を報告し、未reviewをPASSにしません。指摘のないcurrent headの最終検証、push、Draft PR証跡はownerが担います。reviewer UUID、対象SHA、指摘の有無と解消先をPRに記録します。

## セッション構成の観測と限界

2026-09-07の承認済みhandoffで提供されたWindows実験の観測結果を記録します。本改訂で再実験した結果ではありません。

| 観測対象        | 確認されたこと                                                   | この結果では未検証のこと                                                   |
| --------------- | ---------------------------------------------------------------- | -------------------------------------------------------------------------- |
| 並行実行        | 独立したCLI親1本と、その子2本の実行が2.302秒重複した             | 2つのwork groupにFront DeskとPMを加えた合計8本の同時稼働、持続的な処理能力 |
| queue           | 保存操作が成功した                                               | 保存だけで受信・合意・実行開始まで完了すること                             |
| Windowsでの再開 | queueでは自動wakeせず、明示的な `codex exec resume` が必要だった | 自動wake、proactive notification、runtime停止後の監視継続                  |
| exec stdout     | 成功した実行でもspawn呼出しの表示が省略された                    | stdoutにspawnがないことを根拠にした「子がいない」という判定                |

既存設定の `max_concurrent_threads_per_session = 7` は子の設定上限です。primaryを含めた設定上の8本という数と、複数top-level sessionにまたがる「合計8本」の提案は別です。どちらも上記の3本の観測から実証済みとは言えません。この値を変えたり、通常時に枠を埋める指示を加えたりしません。起動済みsessionへの設定反映、completed taskによる枠保持・解放も未検証です。

spawnの有無や親子関係は、session metadataとstructured eventsを照合して確認します。最低限、親・子のsession UUID、起動元、親のspawn要求と結果、対応するtaskを結び付けます。並行実行を主張するときは各実行の開始・終了時刻と共通の重複区間も示します。exec stdoutや最終回答だけでは代用せず、metadataやeventが不足すれば「未確認」とします。観測を報告するときは参照した記録の場所と対象UUIDを添え、確認できた範囲を超えて一般化しません。

今回のhandoffには実験の親子UUIDや元eventの参照先は含まれていないため、ここには推測で補いません。2.302秒という値は提供された観測値として扱い、第三者による元ログからの再計算は未検証です。

## セッション間の同期

既存の `codex queue --thread UUID --message TEXT` の成功は保存完了だけを意味します。受信、合意、起動、作業完了とは区別します。Windows実験ではqueue投入後の自動wakeは起こらず、明示的な `codex exec resume <SESSION_ID>` が必要でした。再開コマンドは[公式の非対話モード資料](https://learn.chatgpt.com/docs/non-interactive-mode)を参照し、対象runtimeで利用できる引数を確認します。

PM/launcherは対象UUIDとpacketを指定して起動・再開し、primary ownerのhandoff応答と実行証跡を回収します。activation待ちがある場合、保存、受付、activation、実行開始、完了をそれぞれ確認します。stdoutの省略を理由に同じ仕事を再投入せず、metadata・structured events・ownerの報告で既存実行を確かめます。元のownerは修正・再reviewを含め完了まで担当を維持します。

Front Deskへの差分snapshotは運用上の報告であり、自動wakeやproactive notificationが確立したことを意味しません。runtimeが停止した後の常駐監視も前提にしません。高頻度polling、自動再投入、不要な定期監視は追加しません。

## 導入と検証

Markdownは既存Prettierとローカルリンク検査で確認し、設定を変更した場合はTOMLも解析します。全リポジトリのチェックは変更担当の責務に従います。文書の検査成功とruntimeの実験結果を区別し、未実行・未観測をPASSと記録しません。

初回導入の担当境界と当時の設定検証は[導入記録](../codex/handoff-pm.md)にあります。現行の責務はこの文書と[PMワークフロー](pm-workflow.md)を使います。
