# ADR 0010: Front Desk依頼registryと明示handover

## 決定

Issueを目的・受入条件・課題状態の正本、PRを差分・検証証跡の正本とする。Status snapshot内の`request_registry`は、参照先と公開用cache、担当、引継状態を結ぶ運用索引に限定する。cacheには観測元と固有時計を付け、Issue/PRを黙って更新しない。

registry更新は既存Status.Ingest principalの`PUT /api/status/requests/upsert`だけから受ける。operator GET権限はIngestへ付与しない。`actor_front_desk`は単一publisher内の競合検出・監査labelであり、認証された個体identityやなりすまし防止境界ではない。新しい権限、secret、daemonは追加しない。

各変更はBlob ETagとregistry `generation`の両方を検査する。指定された`request_id`だけをmergeし、他の依頼を保持する。同一内容はrevision、generation、`updated_at`を更新しない。Issue観測、公開報告、runtime接続は別時計で退行、nonnullからnull、同時刻の異内容を409にする。`scope_id`はregistry内で一意とし、同じagent aliasを複数依頼に含めても`request_id`と`scope_id`で表示内容を分離する。

handoverはprepareとclaimに分ける。prepareはactive Front Deskとworker状態を変えず、後継logical alias、generation、bundle digestをready状態へ保存する。claimはnamed successor、expected generation、digestの全一致時だけactive Front Deskを移す。runtime IDは新context開始後にclaimへ明示して結び、logical aliasと混同しない。claim時、未完依頼は`handover-waiting`かつ`record-only`へ移し、旧runtimeを現在接続済みに見せない。進捗・証跡は保持し、workerを自動dispatchしない。

## Bundleの決定形式

bundle schema version 1は、expected generation、from/to logical alias、prepared_at、`explicit-dispatch-required`、request ID順にsortした公開用のrequest参照と次手を含む。digest欄とserver handover状態はpreimageへ含めない。UTF-8、JSON key辞書順、空白なし、`ensure_ascii=false`で直列化したbytesのSHA-256を`sha256:<lower hex>`とする。timestampは入力済みISO 8601文字列を保持する。prepare後はregistryを更新せず、claimがstaleなら再送せず再prepareする。

receiptは今回提出したrequest、active Front Desk、generation、handoverとopaque revisionだけを返す。保持した他requestを返さず、PUTをGET oracleにしない。通信結果不明時は再送せず、認可済みoperator側の照合へ戻る。

## トレードオフ

同一Ingest principal内のaliasはsecurity identityではないため、悪意あるpublisherの偽装は防げない。一方、既存認可範囲を広げず、事故による二重claimと古い窓口の更新をCASで止められる。workerの再接続は明示dispatchが必要で、handover直後に自動再開しない。
