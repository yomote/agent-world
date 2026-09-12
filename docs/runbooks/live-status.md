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

sourceは次の4種類だけを受け付け、画面にも表示する。

| source               | 意味                                                                               |
| -------------------- | ---------------------------------------------------------------------------------- |
| `codex-event`        | Codex App Server eventをadapterが正規化した入力                                    |
| `local-event-record` | このPCの構造化task event記録をadapterが正規化した入力。App Server live接続ではない |
| `pm-confirmed`       | PMが確認した時点の手動snapshot。runtimeのlive状態とは表示しない                    |
| `fixture`            | 表示・test用。実際のagent稼働とは表示しない                                        |

work itemは従来のagent / role / taskに加え、owner / session / task label、`not-started` / `running` / `review-wait` / `human-wait` / `stopped` / `completed` / `unknown`、task観測時刻、sanitized activity種別と時刻、stale、next action、blockerを持つ。保存済みv1を読むため`idle` / `blocked`も受け付ける。会話本文、reasoning、tool引数・結果、local path、raw session ID、secret、token usageは受け付けない。未知fieldと未知sourceはbackendが拒否する。

writerは検証済みsnapshotを一時fileからrenameして置き換える。履歴は保存せず`artifacts/status/current.json`だけを読む。ブラウザcacheとAPI response cacheは使わない。

現在の手動writerは`pm-confirmed`と`fixture`だけを許可する。`codex-event`と`local-event-record`を拒否し、手動入力をevent観測として保存できない。

## 現在の接続範囲

ローカルHTTP表示、PM確認snapshot、local event記録adapterは実装済み。Codex CLIの`app-server` commandとJSON schema生成機能が存在することは確認したが、この版はDesktopが使用中のApp Server sessionへ接続していない。別App Serverを起動して現在のDesktop / worker状態を取得したとは扱わない。

Azure公開、認証、credential、外部ingest endpointはこのローカル版に含めない。公開時は管理用Resource Group、本人Entra認証、sanitized eventだけを送るcredential境界について別のHITL承認を得る。

Azure公開の構成、停止条件、credential境界、rollbackは[Azure管理status 初回公開packet](azure-management-status.md)を正本とする。packetが承認されるまでlocal collectorとこの画面を維持し、Azure公開済みとは扱わない。
