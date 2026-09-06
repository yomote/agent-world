# 管理statusのローカル表示

管理status最小版は、PMまたはCodex event adapterが保存した最新snapshotを、同一originのローカルbackend経由で表示する。ブラウザからGitHub APIへ接続せず、定期更新は10秒ごとに`GET /api/status`だけを読む。

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

## snapshot契約

sourceは次の3種類だけを受け付け、画面にも表示する。

| source         | 意味                                                            |
| -------------- | --------------------------------------------------------------- |
| `codex-event`  | Codex App Server eventをadapterが正規化した入力                 |
| `pm-confirmed` | PMが確認した時点の手動snapshot。runtimeのlive状態とは表示しない |
| `fixture`      | 表示・test用。実際のagent稼働とは表示しない                     |

work itemはagent label、role、task、`running` / `review-wait` / `blocked`、itemの観測時刻、任意のIssue / PR URLと短いnoteだけを持つ。会話本文、reasoning、tool引数、local path、secret、token usageは受け付けない。未知fieldと未知sourceはbackendが拒否する。

writerは検証済みsnapshotを一時fileからrenameして置き換える。履歴は保存せず`artifacts/status/current.json`だけを読む。ブラウザcacheとAPI response cacheは使わない。

## 現在の接続範囲

ローカルHTTP表示とPM確認snapshotは実装済み。Codex CLIの`app-server` commandとJSON schema生成機能が存在することは確認したが、この版はDesktopが使用中のApp Server sessionへ接続していない。`thread/status/changed`を受けるadapterは次の差分で実装・検証し、それまでは`pm-confirmed`を`codex-event`へ変更しない。

Azure公開、認証、credential、外部ingest endpointはこのローカル版に含めない。公開時は管理用Resource Group、本人Entra認証、sanitized eventだけを送るcredential境界について別のHITL承認を得る。
