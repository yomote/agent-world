# 工場・IaCの引き継ぎ

更新日: 2026-09-06。担当セッション: `01a071e4-8347-7283-80fb-8424c65c36ac`。

## 引き渡し完了

工場・IaCの実装、修正、独立再レビューは、Public `yomote/agent-world` への追加反映を含めて完了している。未解決のレビュー指摘はない。成果と編集対象ファイルの全一覧は [同期回答の取り込み対象](handoff-factory.md#取り込み対象) に記録した。

統合実務担当 **`/root/initial_git_integration` が、列挙した工場・IaC成果と当方の引き継ぎ2文書を初回commitへ取り込んでよい**。以後の統合編集、他担当の変更を含む統合ツリーの最終検証、初回commitは同担当へ引き渡す。本書の最終化で工場側の編集は終了し、実行中の検証や残作業はない。他担当の変更のrevert・stash、工場側でのcommit・pushは行っていない。

以下の検証は工場担当の完了時点の結果であり、その後の統合ツリーの検証結果ではない。GitHub上の未検証事項と認証待ちは下記に残し、統合担当が引き継ぐ。

## 実装

- [棚卸しと検証記録](../factory-adoption.md)にMind Inboxの実装・未導入案と採否を整理。
- `infra/github`にTerraformによるrepo/default branch/ruleset/Actions権限/Dependabot alertsとsecurity updatesの宣言、import、Windows/Linuxのprovider lock、mock policy testを追加。
- API生成物を上書きしない整合性検査、工場検査runnerとログ/JSON/Markdown、lychee、Dependabotを導入。
- [PMからの移管](handoff-pm.md)に沿って `.github/workflows/ci.yml` と `docs/runbooks/ci.md` を統合。1jobのcheck・10分・Draft skip・concurrency・起動イベントを維持。重複factory workflowは削除済み。
- 工場runnerの全体予算は180秒。期限後はnot_runを記録して失敗終了する。CIはquality/docs/iacの全成功を最終stepで要求し、artifactとsummaryに証拠を残す。
- PM移管合意の条件を反映し、lychee本体をバージョン・OS・アーキテクチャ別にキャッシュする。検査は毎回実行し、offline・並列上限2・再試行0。キャッシュ追加後の `npm run check` も全成功。GitHub上でのキャッシュhit/missは未検証。

## 検証

`factory:check` はAPI/lint/format/テスト29件/buildが成功し、lychee本体キャッシュ追加後の `npm run check` もexit 0。Terraform fmt/validate/mock test 1件が成功、lycheeのローカルリンクはエラー0。Solによる独立レビューで見つかった合算タイムアウトの問題は修正し、指摘の解消を確認済み。キャッシュ追加と公開先更新の追加レビューも具体的な問題なし。公開先更新後の文書PrettierとTerraform fmtも成功している。

アプリ実装・依存・画面動作は[アプリ担当の記録](handoff-app.md)が担当する。この工場タスクでアプリの画面検証をやり直したという意味ではない。

## 未実施

公開先は2026-09-06の追加連絡でユーザー指定の **Public `yomote/agent-world`** に確定し、Terraformの入力例にも反映した。新PM側ではGCM認証のGitHub API `/user` が401となり再試行停止、ブラウザー未ログインで、認証復旧はユーザー操作待ち。

引き渡し時点でGitHub remoteの設定、push/Actions上の実行、実GitHubに対するplan/applyと再plan、共有stateへの移行は未実施。Actionsのキャッシュhit/miss、Draft/Readyイベント、artifact保存は実環境で未検証。適用手順と管理対象外の範囲は [GitHub IaC](../../infra/github/README.md)。初回commit・PR本文の準備と認証復旧後の公開作業は `/root/initial_git_integration` へ引き渡す。変更一覧と合意は [同期回答](handoff-factory.md) を参照する。
