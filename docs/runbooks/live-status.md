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

`artifacts/status/local-agents.json`へroot threadと表示対象の対応を置く。このfileはgit管理外であり、`root_thread_id`と`agent_path`を画面やsnapshotへ出さない。各agentには表示を許可した`agent`、`role`、`task`と、任意の`issue_url` / `pr_url`だけを指定する。

```powershell
npm run status:events
```

adapterは`session_meta`がroot threadと`agent_path`へ一意に対応するときだけ起動する。構造化された`task_started`と`task_complete`の時刻・種別だけを状態へ使い、会話本文、reasoning、tool引数から推測しない。`task_complete`はそのturnの終了だけを示し、Issue完了や承認済みを意味しない。開始記録が古いまま終了を確認できない場合は`unknown`、readerの新しい受信が120秒なければsnapshot全体をstaleとする。同じ記録を再読しても`received_at`は更新しない。

これはこのPCのlocal event記録を読む補助adapterであり、DesktopのApp Serverへlive接続したものではない。`review-wait`と`blocked`はPM判断なのでadapterは生成しない。

## snapshot契約

sourceは次の4種類だけを受け付け、画面にも表示する。

| source               | 意味                                                                               |
| -------------------- | ---------------------------------------------------------------------------------- |
| `codex-event`        | Codex App Server eventをadapterが正規化した入力                                    |
| `local-event-record` | このPCの構造化task event記録をadapterが正規化した入力。App Server live接続ではない |
| `pm-confirmed`       | PMが確認した時点の手動snapshot。runtimeのlive状態とは表示しない                    |
| `fixture`            | 表示・test用。実際のagent稼働とは表示しない                                        |

work itemはagent label、role、task、`running` / `idle` / `unknown` / `review-wait` / `blocked`、itemの観測時刻、任意のIssue / PR URLと短いnoteだけを持つ。会話本文、reasoning、tool引数、local path、secret、token usageは受け付けない。未知fieldと未知sourceはbackendが拒否する。

writerは検証済みsnapshotを一時fileからrenameして置き換える。履歴は保存せず`artifacts/status/current.json`だけを読む。ブラウザcacheとAPI response cacheは使わない。

現在の手動writerは`pm-confirmed`と`fixture`だけを許可する。`codex-event`と`local-event-record`を拒否し、手動入力をevent観測として保存できない。

## 現在の接続範囲

ローカルHTTP表示、PM確認snapshot、local event記録adapterは実装済み。Codex CLIの`app-server` commandとJSON schema生成機能が存在することは確認したが、この版はDesktopが使用中のApp Server sessionへ接続していない。別App Serverを起動して現在のDesktop / worker状態を取得したとは扱わない。

Azure公開、認証、credential、外部ingest endpointはこのローカル版に含めない。公開時は管理用Resource Group、本人Entra認証、sanitized eventだけを送るcredential境界について別のHITL承認を得る。

Azure公開の構成、停止条件、credential境界、rollbackは[Azure管理status 初回公開packet](azure-management-status.md)を正本とする。packetが承認されるまでlocal collectorとこの画面を維持し、Azure公開済みとは扱わない。
