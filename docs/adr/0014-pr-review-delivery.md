# ADR 0014: 独立レビューを通常PR reviewへ配送する

- Status: Proposed
- Date: 2026-09-19

## 文脈

独立reviewerの結果はローカルsessionで得ていたが、GitHubにはsummary markerやtimeline commentだけが残る場合があった。これでは個々の指摘がfile/line、対象head、修正後のthread解決へ結び付かず、`PASS`という結論から指摘・影響・対応を追えない。GitHub認証identityと独立reviewerのruntime identityも別であり、PR作者と同じtokenからAPPROVEを作ることは独立性の偽装になる。

## 決定

独立reviewerは構造化findingを返し、実装ownerの既存GitHub認証が`COMMENT`のPull Request Reviewとして代行配送する。findingはcurrent headのdiff位置とACへ結び、1 reviewのinline commentsへまとめる。指摘0件もscope、check、head、0件を通常reviewへ残す。review本文には独立reviewerと代行actorを分けて記録する。

Issue責任者 / PMがACのID、定義、source version、状態を管理し、PR作者が変更固有のreview観点とknown unmetを提示する。両入力がなければreview済みやReadyへ進めない。独立reviewerは作者観点に拘束されず、欠落を指摘できる。AC変更、PM受入、PO価値判断はreview deliveryと分ける。

投稿前にhead、path、line/range、sideをGitHubのcurrent diffで検査する。content keyで既存reviewをread-only照合し、結果不明を自動再送しない。修正後はnew headに対する同じreviewerの確認と元finding IDを条件に、元thread IDを解決する。outdated findingを新しいlineへ付け替えない。

## トレードオフ

通常reviewによりGitHub UIで指摘、修正、解決を追える。一方、投稿者は独立reviewer本人ではないためGitHubのAPPROVED状態を作らず、branch protectionのapproval要件を満たす証拠にはならない。API request数とsecondary rate limitの影響も増えるため、findingsを1 reviewへ束ね、paginationや結果不明では停止する。

実findingがない状態で架空inlineを投稿しない。zero-finding COMMENT reviewは実利用確認できるが、line threadの実GitHub E2Eは次の本当のfindingまで未検証として残す。
