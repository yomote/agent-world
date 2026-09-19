# 0003. CIの重複実行と開発エージェントの外部照会を抑える

- Status: Accepted
- Date: 2026-09-05

## 背景

以前のプロジェクトではCI/CDの頻繁な起動と、開発エージェントからの取得処理が重なり、ボット判定による停止が起きたとの報告があった。本リポジトリには当時のエラーログはなく、原因がAPIの一次制限・二次制限・別の拒否のどれかは確定していない。

現在のCIはPRとmain / masterへのpushで1jobを実行する。CD・定期巡回・外部データ収集はないが、連続更新の古い実行を止める設定がなく、Draft中も全チェックが動く。

## 決定

- GitHub Actionsの標準 `concurrency` で、同じworkflowのPR番号またはブランチをグループにし、古い実行を取り消す。別PRのチェックを相互に取り消さない。
- Draftではjobをskipし、`ready_for_review` で検証する。`converted_to_draft` も受け付け、古い実行をキャンセル対象にする。
- PR更新とmain / masterの検証は、read-only権限のworkflowで扱う。定期起動、コメント駆動、自動再実行、CIから別のCIを起動する連鎖は追加しない。実行予算、照会、制限応答の扱いは[CIと外部アクセスの運用](../runbooks/ci.md)と[AGENTS.md](../../AGENTS.md)を正本とする。

## トレードオフ

- `concurrency` は古い実行を取り消すが、workflowの作成数や異なるPR間の同時実行数は制限しない。
- Draftでもworkflowの記録は残るが、jobを省くので依存の取得と検証にrunnerを使わない。Ready for reviewまではリモート検証が遅れるため、ローカルの `npm run check` を使う。
- main / masterの検証はPRと近い内容を再検証する場合があるが、統合後の状態を確認するため残す。
- 文書のみの変更も整形確認が必要なのでpath filterは使わない。必須チェックをworkflowごとskipしてPendingにする問題も避ける。
- 外部APIの予算を共通レート制御や監視基盤として先に実装せず、GitHubの非公開・変動する制限を回避できる保証はしない。

## 参照

- [GitHub Actionsの同時実行制御](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency)
- [pull_requestイベント](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#pull_request)
- [GitHub REST APIの推奨運用](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api)
