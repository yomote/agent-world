# Front Desk依頼registry運用

## 更新境界

bundle用CLIはPython 3.11の標準libraryだけで動き、専用venvや追加installを必要としない。リポジトリrootから`python scripts/manage_status_registry.py ...`を実行する。APIをTestClientで隔離検証する場合だけ、[ローカル開発](local-dev.md#依存の再現)の既存declared dependenciesを使う。

- Issueは目的・受入条件・状態、PRは差分・検証証跡の正本とする。registryはURLと観測時刻を持つ索引である。
- Status.Ingest publisherが`PUT /api/status/requests/upsert`を1回送る。operator用GET権限を流用しない。
- 422は入力不正、409はgeneration・状態・内容時計・Blob ETag競合、503は保存済みsnapshotの読取または書込障害である。receiptがない通信結果不明時は再送しない。
- `runtime_connection=record-only`または`unknown`をrunningや再接続済みに変換しない。APIはworkerをdispatchしない。

## project rootからのbootstrap

新しいtop-level root sessionは`AGENTS.md`を入口にする。Front Deskは受付だけを行い、PMが次のread-only発見をworkerへ委任する。subagent、child、既存worker、旧rootはこの入口からclaimしない。

`python scripts/manage_status_registry.py discover --locator .codex/handoff-locator.local.json --output .codex/bootstrap-result.local.json`

locatorはproject rootの`.codex/handoff-locator.local.json`だけを使い、gitへcommitしない。日付付きcheckoutや一時venvを参照せず、次のschema version 1を使う。

```json
{
  "schema_version": 1,
  "project_id": "yomote/agent-world",
  "repo_url": "https://github.com/yomote/agent-world",
  "registry_source_commit": "<request registryを含む40桁commit>",
  "runbook": "docs/runbooks/request-registry.md",
  "cli": "scripts/manage_status_registry.py",
  "bundle_path": "<stable absolute bundle path>",
  "start_path": "<stable absolute start document path>",
  "context_path": "<stable absolute public context path>",
  "context_digest": "sha256:<64 lowercase hex>",
  "expected_generation": "<claimで使うpositive generation>",
  "bundle_digest": "sha256:<64 lowercase hex>",
  "from_front_desk": "<current logical alias>",
  "to_front_desk": "<successor logical alias>",
  "claim_executed": false,
  "claim_policy": "new-top-level-root-explicit-claim-only"
}
```

locatorにtoken、cookie、certificate、会話本文を入れない。`context_path`は最終registry snapshotとprepare receiptから作った公開情報だけを持ち、目的、受入条件、owner、進捗、阻害、次手、証跡、報告時刻をrequestごとに復元する。CLIはcontextのcanonical digest、bundle request ID集合、generation・digest・from/to・ready・dispatch policyとの一致も検証する。

`registry_source_commit`はregistry/CLI契約の固定sourceであり、bootstrap入口を追加したcommitとは分ける。CLIはproject ID、固定したrunbook/CLI path、git内のsource commitと両pathのblob、凍結bundleのgeneration・digest・from/to、claim未実行を照合する。source objectがlocalにない場合はworkerがoriginからそのcommitだけをfetchし、再度照合する。

`local_handover_candidate=true`はlocal artifactの整合だけを示す。server側のactive owner、generation、ready状態は未確認で、owner移転やworker起動を意味しない。claim payloadはlocatorの`registry_source_commit`を固定したclean detached checkoutから生成する。primary workspaceがdirtyならcheckout/resetせず、存在しない専用pathへ`git worktree add --detach <new-path> <registry_source_commit>`で作る。

locatorなしは「引継なし」と表示する。別project、既claim、不一致、読取失敗、他のactive owner、接続状態不明はFront Deskの受付を維持したまま受取りを保留する。workerはruntime metadataの`CODEX_THREAD_ID`と自身の`CODEX_SESSION_ID`が異なり、canonical task pathが新rootから委任されたchildを示すことを確認してから`claim-root`へ進む。ユーザーがruntime IDを転記したりCLIを実行したりする手順にはしない。payloadを1回だけ送り、409、422、503、timeout、結果不明では再送せず、locatorも変更しない。

成功receiptでgeneration、active Front Desk、runtime ID、accepted handover、digestを確認したworkerだけがlocal markerを更新する。送信したclaim payloadも渡し、receiptのruntime IDをexact照合する。

`python scripts/manage_status_registry.py mark-claimed --claim-payload <sent-claim.json> --receipt <successful-claim-receipt.json> --output .codex/claim-marker-result.local.json`

marker更新後に初めてowner移転済みとして扱い、必要な未完依頼のworkerだけを明示dispatchする。`mark-claimed`はworkerを起動せず、locator自体も変更しない。locator bytesのdigestとbundle digestに紐づく`.codex/handoff-claim.local.json`をatomicに保存するため、将来prepareでlocatorを置換しても古いclaim markerが新handoverを使用済みにしない。

将来新しいhandoverをprepareした場合は、prepare receiptを確認した担当workerが同じstable locatorを新しいbundle、public context、両digest、generation、from/toへ明示更新する。`discover`はlocatorやregistryを更新しない。新規root sessionを開いただけでは処理は始まらず、最初のユーザーmessageで`AGENTS.md`が適用された後にこの分配を行う。

## 通常更新

未完requestの継続条件と、`completed` 保存前の独立closure audit、PO確認が必要な場合のacceptance receiptは[PMワークフロー](pm-workflow.md#継続と受入closure)を正典とする。`initialize` / `update` は新しい完了自己申告を保存前に検査する。着工時に必須requirement ID、contract digest、DoD版、成果headを保存し、完了遷移で同じ契約とauditを照合するため、直接completedとして初期化したり契約を縮小したりできない。handoff `prepare` は未完requestのowner、next action、resume triggerとblocked reasonを検査する。導入前の保存snapshotは読取り可能なままにし、新しい保存・export入口だけをguardする。

`initialize`は未初期化時に`expected_generation=0`で一度だけ使う。以後はactive Front Desk aliasと現在generationを指定して`update`する。指定requestだけが更新され、他requestは保持される。同内容はno-opとなる。

operatorが取得済みのregistry JSONから公開用の復元一覧を確認する場合は次を使う。CLIはnetworkへ接続せず、IngestにGET権限を与えない。

`python scripts/manage_status_registry.py read --registry registry.json --output registry-summary.json`

## 引継

1. Issue/PRの最終証跡と各依頼の次手をregistryへ先に反映する。
2. operatorが既知のregistry JSONをprivate local fileへ保存し、次でbundleとprepare payloadを作る。

   `python scripts/manage_status_registry.py prepare --registry registry.json --successor front-desk-next --observed-at <ISO8601> --output handover.json`

3. `handover.json`の`update`だけをStatus.Ingest publisherが送る。prepare receiptのgeneration・digest一致を確認する。ここからclaimまでregistryを更新しない。
4. ユーザーが新しいFront Desk contextを開始した後、そのcontextはprivate bundleを読み、次でclaim payloadを作る。

   `python scripts/manage_status_registry.py boot --bundle handover.json --output boot.json`

   `python scripts/manage_status_registry.py claim-root --bundle handover.json --actor front-desk-next --canonical-task-path <delegated-worker-path> --observed-at <ISO8601> --output claim.json`

5. claim payloadを1回送る。stale generation、別successor、digest不一致、二重claimは409で停止する。旧workerを再起動せず、registryにあるIssue、次手、証跡を復元して明示dispatchを待つ。
6. 成功receiptのgeneration、active alias、runtime ID、from/to、digest、`changed=true`を送信payloadとexact照合する。成功時だけmarkerを保存して明示dispatchへ進む。local event collectorは既定の`.codex/handoff-claim.local.json`からclaim generation、alias、runtime IDを読み、runtime IDをSHA-256 digestへ変換した`runtime_binding`を自動付与する。claim generationはowner epochとして通常の依頼更新generationとは別に保持される。raw runtime IDはstatus payload、公開GET、画面へ出さない。prepare bundleへ未知のruntime IDを作らない。

`CODEX_THREAD_ID`とcanonical task pathの検査はruntimeが渡したmetadataの取り違えを防ぐためのprovenance検査であり、APIがrootの真正性を暗号学的に証明するものではない。API側の信頼境界は既存Status.Ingest認証、registry CAS、成功receiptのexact照合である。

未完依頼が0件ならその事実を表示し、dry-run用の架空依頼を本番registryに作らない。dry-runはsnapshotの隔離copyでprepare/claimを検証し、本番active ownerを移さない。
