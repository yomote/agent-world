# 管理statusのローカル表示

管理status最小版は、PMまたはlocal event adapterが保存した最新snapshotを、同一originのローカルbackend経由で表示する。ブラウザからGitHub APIへ接続せず、定期更新は10秒ごとに`GET /api/status`だけを読む。

## 起動

最初に正規化済みwork item配列をwriterの標準入力または`--input`へ渡す。`observed_at`は元の状態を確認した時刻であり、writerが保存時刻を`received_at`へ別に記録する。

```powershell
Get-Content .\snapshot-input.json -Raw |
  python scripts/python_env.py scripts/write_status_snapshot.py `
    --source pm-confirmed `
    --observed-at 2026-09-06T12:45:04Z
npm run status:dev
```

[ローカル画面](http://127.0.0.1:8010/)を開く。120秒を超えて新しいsnapshotを受信していなければstaleを表示する。serverはloopbackだけにbindし、認証なしでLANやInternetへ公開しない。

### local event記録から更新する

`artifacts/status/local-agents.json`へroot threadと表示対象の対応を置く。このfileはgit管理外であり、`root_thread_id`、`session_id`、`parent_thread_id`、`agent_path`を画面やsnapshotへ出さない。各agentには実測したmetadataと、公開を許可したowner / session / task label、任意のIssue / PR、next action、blockerだけを指定する。既存configとの互換用に`agent`、`role`、`task`は残す。

必要な場合だけ、同じconfigの`runtime_capacity`へ明示供給済みの集計metadataを置ける。これはlocal eventから推測しない。`scope`、capacity自身を観測した`observed_at`、状態集計の`state_source: "runtime-list-agents-metadata"`、上限の`limit_source: "runtime-instructions"`、取得できた`running` / `idle` / `completed` / `total` / `max_concurrent_agents`だけを受け付ける。未取得の状態を0にせず、known状態の合計が`total`を超える入力は拒否する。snapshotの再送やtask activity更新はcapacityの観測時刻を更新しない。

```powershell
npm run status:events
```

adapterは起動時に各fileの先頭`session_meta`だけを調べ、設定したsession ID、parent ID、agent pathへ一意に対応するときだけ開始する。以後は選んだfileの追記分だけをbyte offsetから読む。`task_started` / `task_complete`を状態の根拠にし、`item_completed`はtimestampとtypeだけを長いturnのactivity観測に使う。本文、reasoning、tool引数・結果はsnapshotへ送らず、そこから状態を推測しない。

`task_complete`はturnの停止であり、課題全体の完了へ格上げしない。開始後のactivityが120秒を超えて途絶し、終了も確認できない場合は`unknown`へ変える。source側event時刻は`observed_at` / `latest_activity_at`、収集時刻は`received_at`として分ける。同じ記録を再読しただけなら保存しないが、同じtask eventに対するrunning→unknownのstale遷移は新しいsnapshotとして保存する。

既存v1 Azure APIへ最初のsnapshotを入れる間は、追加fieldを除いた互換shapeを明示的に生成できる。

```powershell
python scripts/python_env.py scripts/sync_status_from_local_events.py `
  --config artifacts/status/local-agents.json `
  --output artifacts/status/current.json `
  --compat-v1
```

これはこのPCのlocal event記録を読む補助adapterであり、DesktopのApp Serverへlive接続したものではない。`review-wait`と`blocked`はPM判断なのでadapterは生成しない。

## snapshot契約

sourceは次の5種類だけを受け付け、画面にも表示する。

| source               | 意味                                                                               |
| -------------------- | ---------------------------------------------------------------------------------- |
| `codex-event`        | Codex App Server eventをadapterが正規化した入力                                    |
| `local-event-record` | このPCの構造化task event記録をadapterが正規化した入力。App Server live接続ではない |
| `ingest-upsert`      | serverが既存rowを保持し、認可済みingestの指定項目だけを反映した入力                |
| `pm-confirmed`       | PMが確認した時点の手動snapshot。runtimeのlive状態とは表示しない                    |
| `fixture`            | 表示・test用。実際のagent稼働とは表示しない                                        |

work itemは従来のagent / role / taskに加え、owner / session / task label、`not-started` / `running` / `review-wait` / `human-wait` / `stopped` / `completed` / `unknown`、task観測時刻、sanitized activity種別と時刻、stale、current action、progress summary、その要約の更新時刻、next action、blockerを持つ。current actionとprogress summaryはPMまたは設定ownerが公開用に要約した任意fieldであり、event本文から推測しない。画面は要約を手動更新の公開用メモとしてactivity時刻と分け、値または要約更新時刻がなければ「未取得」と表示する。保存済みv1を読むため`idle` / `blocked`も受け付ける。

`runtime_capacity`はtask件数・担当人数と別のoptional snapshotである。画面は「このセッション：実行中 X / 同時実行上限 Y」を表示するが、runtimeのturn状態であり、進捗率・実作業人数・空き枠を示さない。scope、二つのsource、観測時刻と経過時間を併記し、capacity未取得と古い観測を区別する。存在総数は実行中に換算せず、`completed`を`idle` / `running`へ換算しない。会話本文、reasoning、tool引数・結果、local path、raw session ID、secret、token usageは受け付けない。未知fieldと未知sourceはbackendが拒否する。

画面最上部の`focus_summary`は、今回の作業目的、確認済み進捗、阻害、次の行動、要約自身の`updated_at`と`manual-public-summary` sourceを持つoptionalな明示入力である。担当rowや古いactivityから自動合成しない。省略時は「未取得」を表示し、snapshotの`received_at`を要約の更新時刻へ流用しない。

親子・委任関係はwork itemの`parent_relation`を使う。`root`は依頼元なしの作業窓口、`delegated`は`parent_agent`、`parent_source`、`parent_observed_at`が揃った明示関係、`unknown`は未取得である。role名から親を推測しない。snapshot外の親は許容してその旨を表示し、self-parentとsnapshot内の循環は拒否する。`instruction_summary`は内部prompt本文ではなく公開を許可した指示要約で、既存の`summary_updated_at`と同じ公開メモclockで更新する。

writerは検証済みsnapshotを一時fileからrenameして置き換える。履歴は保存せず`artifacts/status/current.json`だけを読む。ブラウザcacheとAPI response cacheは使わない。

Azure版の`PUT /api/status/upsert`は`local-event-record`のrequestを受け、`agent`が一致する完全なwork itemだけを置換し、新しい`agent`は末尾へ追加する。未指定rowと、省略した`runtime_capacity`はserver側で保持する。capacityは非null値を明示した場合だけ置換する。保存snapshotのsourceは`ingest-upsert`となり、保持した手動rowまで新しいeventから自動取得したとは表示しない。古いrow/capacity観測、重複agent、未初期化snapshot、Blob ETag競合は409で停止する。receiptは今回指定した保存済みrow、指定したcapacity、変更有無、opaque revisionだけで、保持した他rowやsnapshot全体をingestへ返さない。同内容は保存時刻を更新せずno-opにする。

itemの状態観測、activity、公開メモ、親関係、capacity、`focus_summary`はそれぞれの時刻を独立したclockとして扱う。同じclockで内容が異なるrequestや、既存のnonnullなclockをnullへ戻すrequestは409にする。`focus_summary`と親関係はupsertで省略した場合だけ保持され、full PUTでの暗黙消去は拒否する。`ingest-upsert`が一度保存された後は、後続のfull `PUT /api/status`も既存agentを省略して暗黙削除できず409になる。明示的なrow削除APIはこのscopeに含めない。新しいactivity clockを伴う既存のrunning→unknown stale遷移はfull PUTでも維持する。

現在の手動writerは`pm-confirmed`と`fixture`だけを許可する。`codex-event`と`local-event-record`を拒否し、手動入力をevent観測として保存できない。

## 現在の接続範囲

ローカルHTTP表示、PM確認snapshot、local event記録adapterは実装済み。Codex CLIの`app-server` commandとJSON schema生成機能が存在することは確認したが、この版はDesktopが使用中のApp Server sessionへ接続していない。別App Serverを起動して現在のDesktop / worker状態を取得したとは扱わない。

Azure公開、認証、credential、外部ingest endpointはこのローカル版に含めない。公開時は管理用Resource Group、本人Entra認証、sanitized eventだけを送るcredential境界について別のHITL承認を得る。

Azure公開の構成、停止条件、credential境界、rollbackは[Azure管理status 初回公開packet](azure-management-status.md)を正本とする。packetが承認されるまでlocal collectorとこの画面を維持し、Azure公開済みとは扱わない。
