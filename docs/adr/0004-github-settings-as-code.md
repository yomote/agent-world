# 0004. 開発工場とGitHub設定を宣言から再現する

- Status: Accepted
- Date: 2026-09-06

## 背景

ユーザーから、Mind Inboxで有効だった工場機能と未導入案を棚卸しして取り込むこと、GitHub設定もIaCで管理することが指示された。Mind Inboxでは自作設定同期とTerraformが併存し、宣言・検証・実環境への適用が一致していない箇所があった。

## 決定

- GitHub設定の正本を `infra/github` のTerraformにする。独自の設定同期スクリプトを重ねない。
- repo設定、main ruleset、Actions権限、依存脆弱性の通知と修正提案を管理する。GitHubが直接読むworkflow/Dependabot設定はYAMLのまま管理する。
- importを伴うplanで既存設定を確認し、確認したplanをapply、再planで差分なしを検証する。fmt/validate/mock testだけでは適用済みとしない。
- 最初の検査はAPI生成物の一致、文書リンク、CIと必須チェック名の一致、既存の品質検査。結果をログとsummaryへ残す。
- [ADR 0003](0003-bounded-ci-and-external-access.md)のDraft skip、concurrency、1job・10分、read-only、取得予算を維持して既存CIに統合する。

## トレードオフと現時点の限界

設定の適用には対象repo・公開範囲・管理API認証が必要。ローカル検証を完了しても、これらが未確定なら実環境との一致は未検証のままとする。初期stateはローカルで、共有運用に移る前に保管・暗号化・ロックを備えたbackendへ移行する。

独立レビューの自動起動、定期drift検知、二重着工防止、クラウド配備はそれぞれの前提が揃ってから追加する。今回の[棚卸し](../factory-adoption.md)に根拠と着手条件を残す。
