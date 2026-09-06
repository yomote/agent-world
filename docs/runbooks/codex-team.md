# Codex チーム運用

この文書は、開発作業をSol Front Desk、Astra PM controller、Sol / Terra / Lunaのworkerで分担するための運用です。ゲーム内のAgent / World設計とは無関係です。

## 設定の配置

設定の正本は [`.codex/config.toml`](../../.codex/config.toml) と `.codex/agents/` の5つの役割定義です。project既定の窓口modelはSol / medium、`pm_controller`はAstra / high。設定は起動済みsessionへ遡及適用されるとは限らず、現在taskのmodel変更を行う公開toolはこの導入時点で確認できていない。新しいtaskでproject設定を読み、既存sessionでは利用できる協調toolで子のmodelと推論強度を明示する。

Front DeskはSol / mediumで起動し、即時受付、既知の状態表示、既決事項の回答、routineな修正・再reviewの既存packet引継ぎを行う。新規開発、roadmapやpriority変更、複数Issueや担当の依存、scopeやDoD変更、blocked、ownership conflict、受入れ・次release判断、定期的な状態reconciliationは`pm_controller`へ渡す。Astra PMは長期方針と実行packetを管理し、Front Deskは技術方針やpriorityを独断で再設計しない。どちらもhands-on作業を行わない。

ユーザーは単一のFront Deskだけに指示・承認・進捗確認を行います。Astra PMは実装、調査、検証、統合をworkerへ、実装会話を渡さない確認をreviewerへ分配し、Front Deskが結果をユーザーへ取り次ぎます。個別workerをユーザーが巡回して管理する運用にはしません。実務の流れ、分配packet、Git運用、Remote継続、承認境界は [PMワークフロー](pm-workflow.md) を正典とし、この文書には起動・設定の詳細だけを置きます。

## 分配前

1. `AGENTS.md` とセッション間の担当を確認し、独立したタスクだけを分配する。
2. 依頼には目的、完了条件、担当ファイル、禁止範囲、依存関係、検証、報告形式を明記する。
   許可済みの外部操作も同じpacketに含め、workerが途中で判断できる状態にする。
3. 作業中の同じファイルは触らない。重なる場合は担当 PM と合意し、個別 worktree などで隔離してから着手する。初回コミット前は worktree の作成を前提にせず、重ならない範囲だけを扱う。

共有ディレクトリは自動で worktree に隔離されません。無関係なファイルを restore、stash、commit しません。Git コミットや外部投稿は、そのタスクの権限と担当者間の同期後に行います。

## 役割

| 役割          | モデル・推論             | 使う場面                                       |
| ------------- | ------------------------ | ---------------------------------------------- |
| Front Desk    | Sol / medium             | 即時受付、既知状態、既決事項、結果の平易な集約 |
| implementer   | Sol / medium             | 複雑な実装。必要なら PM が high を明示して依頼 |
| terra_worker  | Terra / medium           | 範囲と完了条件が明確な通常実装                 |
| scout         | Luna / medium、read-only | 限定した検索、調査、抽出                       |
| reviewer      | Sol / high、read-only    | 独立したレビュー。実装会話は継承しない         |
| pm_controller | Astra / high、read-only  | 長期方針、優先順位、依存、owner、DoD、状態管理 |

全役割は、成果、根拠または対象ファイル、検証、未完了を短く返します。Luna が難航したら反復せず Terra または Sol へ引き上げます。

## Astra PMを起動する基準

Front Deskは次の管理節目で、事実、制約、選択肢、期限を`pm_controller`へ渡す。

- 新しい開発依頼、roadmapやpriorityの変更、複数Issueや担当の依存を扱う。
- scopeやDoDを変える、または複数領域にまたがる長期architectureを決める。
- data、security、費用への影響が重大で、後から戻す費用が高い。
- 相反する制約があり、複数案のtradeoff評価が必要である。
- workerのblocked、ownership conflict、受入れ、次release、状態reconciliationを判断する。
- 障害原因に複数の有力仮説が残り、Solの調査と独立reviewでも収束しない。

Astra PMはpriority、dependency、owner、DoD、停止条件、状態遷移を含むpacketを返す。承認者として形式gateを増やさず、ユーザーが決めるproduct scopeはFront Deskを通して確認する。単純なstatus質問、既知事実、受付、既存packet内のroutine修正・再reviewはFront Deskが即答・継続し、Astra応答待ちでも「受領、既知の現在地、管理判断を確認中」を先に返す。

このrepoでは、Worldのauthority、永続化、複数workerの整合性を同時に変える設計、production OIDCとshared stateと継続費用の境界を再設計する判断、token event・ruleset・deployの複数仮説がSol review後も残る障害分析が該当する。既存packet内のmove validation test、formatter修正、Issue状態更新、採用済み方針どおりのGitHub environment policy追加はFront Deskが既存ownerへ引き継げる。

コストと速度は、必要な役割と文脈だけを使うことで管理する。Lunaは限定した読み取り調査、Terraは通常実装、SolはFront Desk・複雑実装・独立review、Astraは管理節目だけを担う。子へ渡す文脈は目的達成に必要な最小限に絞る。独立した作業だけを並列にし、調査と全体検証を重複させない。最終チェックは統合workerへ一元化する。難航したworkerは無限に反復せず、状況をAstra PMへ返して担当の引き上げ判断を受ける。金額と高速化率は未測定のため主張しない。

`implementer` の起動時は推論強度も毎回明示します。通常は `medium`、難所は `high` とし、カスタム定義では強度を固定しません。ほかの役割は定義内の強度が優先されるため、呼び出し時の指定だけで変更できるとは扱いません。

## 並行数とレビュー

2026-09-06に、子の上限`max_concurrent_threads_per_session`を3から7へ変更した。primaryを含めた設定上の最大は8である。これは同時に8本を常駐させる指示ではなく、独立した実装・review・調査を必要なときに並行できる余地である。通常は2〜3子を使い、委任のためだけに細分化しない。

設定変更が起動済みsessionへ即時反映されるか、completed taskが一時的に枠を保持したときの解放動作は未検証である。上限値だけで実効枠の増加を保証せず、新しいsessionまたは実際の起動結果で確認する。モデル役割、権限、sandboxはこの変更では変えない。

枠が空いてから reviewer に独立レビューを依頼します。組み込み `worker` との衝突を避けるため、通常実装役は `terra_worker` を使います。

この導入セッションの `spawn_agent` では、モデル変更と全履歴継承は併用できません。`fork_turns = "none"` または必要な直近履歴だけを選び、目的、仕様、担当、完了条件を独立した依頼として渡します。独立 reviewer は PM が `fork_turns = "none"` で生成します。別のクライアントでは利用できる起動引数を確認し、同じ意図で新しい文脈を用意します。

## セッション間の同期

長期状態を会話だけに置かず、目的・DoD・状態・owner・次手順はIssue、差分と証跡はPR、architecture判断はADR、運用判断はrunbookを正本とする。session再開時はこれらから状態を復元する。既存の `codex queue --thread UUID --message TEXT` で連絡できますが、queue の完了は受信・合意を意味しません。担当者ごとの handoff で確認します。高頻度 polling や不要な定期監視は作りません。

## 導入と検証

最初は調査と独立レビューから導入し、次に小さく明確な実装へ広げます。設定は TOML として解析し、Markdown は既存 Prettier で確認します。全リポジトリのチェックは変更担当の責務に従います。

設定形式と継承順序は [OpenAI公式のSubagents資料](https://learn.chatgpt.com/docs/agent-configuration/subagents) を参照します。初回導入の担当境界と共通規約への申し送りは [導入記録](../codex/handoff-pm.md) にあります。
