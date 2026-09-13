# Front Desk依頼registry運用

## 更新境界

bundle用CLIはPython 3.11の標準libraryだけで動き、専用venvや追加installを必要としない。リポジトリrootから`python scripts/manage_status_registry.py ...`を実行する。APIをTestClientで隔離検証する場合だけ、[ローカル開発](local-dev.md#依存の再現)の既存declared dependenciesを使う。

- Issueは目的・受入条件・状態、PRは差分・検証証跡の正本とする。registryはURLと観測時刻を持つ索引である。
- Status.Ingest publisherが`PUT /api/status/requests/upsert`を1回送る。operator用GET権限を流用しない。
- 422は入力不正、409はgeneration・状態・内容時計・Blob ETag競合、503は保存済みsnapshotの読取または書込障害である。receiptがない通信結果不明時は再送しない。
- `runtime_connection=record-only`または`unknown`をrunningや再接続済みに変換しない。APIはworkerをdispatchしない。

## 通常更新

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

   `python scripts/manage_status_registry.py claim --bundle handover.json --actor front-desk-next --runtime-session-id <new-runtime-id> --observed-at <ISO8601> --output claim.json`

5. claim payloadを1回送る。stale generation、別successor、digest不一致、二重claimは409で停止する。旧workerを再起動せず、registryにあるIssue、次手、証跡を復元して明示dispatchを待つ。
6. 新contextのruntime IDが得られた後、logical Front Desk aliasとの対応を公開報告で更新する。prepare bundleへ未知のruntime IDを作らない。

未完依頼が0件ならその事実を表示し、dry-run用の架空依頼を本番registryに作らない。dry-runはsnapshotの隔離copyでprepare/claimを検証し、本番active ownerを移さない。
