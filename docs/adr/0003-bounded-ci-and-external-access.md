# 0003. CIの重複実行と開発エージェントの外部照会を抑える

- Status: Accepted
- Date: 2026-09-05

## 背景

以前のプロジェクトではCI/CDの頻繁な起動と、開発エージェントからの取得処理が重なり、ボット判定による停止が起きたとの報告があった。本リポジトリには当時のエラーログはなく、原因がAPIの一次制限・二次制限・別の拒否のどれかは確定していない。

現在のCIはPRとmain / masterへのpushで1jobを実行する。CD・定期巡回・外部データ収集はないが、連続更新の古い実行を止める設定がなく、Draft中も全チェックが動く。

## 決定

- GitHub Actionsの標準 `concurrency` で、同じworkflowのPR番号またはブランチをグループにし、古い実行を取り消す。別PRのチェックを相互に取り消さない。
- Draftではjobをskipし、`ready_for_review` で検証する。`converted_to_draft` も受け付け、古い実行をキャンセル対象にする。
- PR更新とmain / masterの検証、1job・10分のtimeout・read-only権限・依存キャッシュを維持する。定期起動、コメント駆動、自動再実行、CIから別のCIを起動する連鎖は追加しない。
- 開発エージェントはpushをまとめる。状態照会は対象を絞り、60秒以上の間隔・runあたり最大10回、ログ取得は失敗箇所だけにする。制限応答の待機指示を守り、読み取りの再試行は最大2回で止める。詳細は [CI運用](../runbooks/ci.md) と [AGENTS.md](../../AGENTS.md) に置く。

## トレードオフ

- `concurrency` は古い実行を取り消すが、workflowの作成数や異なるPR間の同時実行数は制限しない。pushをまとめる運用が必要。
- Draftでもworkflowの記録は残るが、jobを省くので依存の取得と検証にrunnerを使わない。Ready for reviewまではリモート検証が遅れるため、ローカルの `npm run check` を使う。
- main / masterの検証はPRと近い内容を再検証する場合があるが、統合後の状態を確認するため残す。
- 文書のみの変更も整形確認が必要なのでpath filterは使わない。必須チェックをworkflowごとskipしてPendingにする問題も避ける。
- API照会の予算はAgent規約であり、プログラムによる強制ではない。現時点で外部APIクライアントがないため、共通レート制御や監視基盤を先に実装する複雑さを避ける。
- GitHubの制限には非公開・変動する条件があり、一定の回数ならボット判定されないという保証はできない。実行頻度を抑え、拒否時に停止する方針とする。

## 参照

- [GitHub Actionsの同時実行制御](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency)
- [pull_requestイベント](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#pull_request)
- [GitHub REST APIの推奨運用](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api)
