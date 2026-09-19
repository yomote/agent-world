# 独立レビューのGitHub配送

独立reviewerの結果は、timelineの要約だけでなくGitHubの通常Pull Request Reviewとして配送する。実装ownerのGitHub認証が代行記録するため、reviewer本人のGitHub認証やGitHub上の承認とは扱わない。

## 入力契約

reviewは固定40桁head、reviewer、scope、実施したcheck、verdictとfindingsを持つ。各findingは次を持つ。

review入力には、Issue責任者 / PMが管理するrequirement/acceptance ID、定義、source、source version、状態、evidenceの対応表と、PR作者が提示する変更固有のreview観点（risk、business invariant、確認点、evidence、known unmet）を含める。どちらかが欠ければreview済みやReadyへ進めない。作者観点は独立reviewerを拘束せず、作者の自己申告でAC達成にしない。ACを変える場合はIssue責任者 / PMが理由、履歴、既存結果への影響をIssueへ残す。PM受入とPO価値評価は別である。

automation campaignでは、checkout内のJSONを`python -m scripts.automation.delivery <campaign> review-input --source <checkout相対path>`で一度固定する。入力はPR用のscope ID/定義とscope Issue、そのscopeで必須のacceptance IDs、親Issueに残るDoDのIssue/owner/再開triggerを分ける。acceptance IDは一意で、必須ACはscope Issueへ結び付ける。deliveryはこの入力をDraft PR本文へ公開し、同じ値をreviewerへ渡す。既存入力の上書きや、PR作者によるACの条件変更は受け付けない。独立reviewを配送しても、PR scopeの必須ACがすべて`achieved`でなければReadyとmergeへ進まない。親Issueに残るDoDはPR scopeを満たした部分変更のmergeを妨げないが、Issue完了にも使わず、merge後も閉じない。

- 一意finding ID、requirement ID、acceptance ID
- PR diff上のpath、lineまたはrange、`LEFT` / `RIGHT`
- 問題、具体的な失敗シナリオ、影響、根拠、要求する対応
- `P0` / `P1` / `P2`、blocking根拠
- `generic_risk` またはscope固有の `business_invariant`

好みをblockerにしない。scope外の案、duplicate、stale findingはinlineへ付け替えず、理由付きのsuppressionとしてsummaryへ残す。該当しない観点を一律に要求せず、必要ならcheck側で`not_applicable`と理由を記録する。semantic review、domainの型・property test、PO価値評価は別の証拠であり、CI成功を目的達成の証明にしない。Issue DoDからevidence/head/checker/resultへの対応はgovernance側の正本を参照し、review本文へ同じ表を複製しない。

## 配送と再確認

1. current PR headとreview headを一致させ、GitHubが返したchanged filesのpatchでpath、line、range、sideを検証する。
2. `POST /pulls/{number}/reviews`へ`event=COMMENT`で1 reviewを作り、findingsをinline commentsとして束ねる。指摘0件ならscope、check、head、0件をsummaryに残し、架空inlineは作らない。
3. review bodyのcontent keyを既存reviewsから検索する。同じkey、head、完全一致body、代行actor、`COMMENTED` state、対象repository/PRのreview URLが一致するreceiptだけを再利用し、再投稿しない。content keyにはscope定義、必須AC、親残DoDを含む完全なreview契約を入れる。files取得後、POST直前、receipt取得後にもPR headを再確認する。write結果不明では自動再送せず、`reconcile-review-delivery`を明示実行し、保存したoperation ID/key/headとremote receiptの一致だけをread-only確認する。receiptがなければ停止を維持する。
4. visible review URLとheadを保存してから`reviewed`またはreview passへ進む。従来のindependent-review markerはmerge gate用の索引であり、通常reviewの代替にしない。
5. 修正でheadが変われば同じreviewerが新headを再reviewする。元threadを新lineへ付け替えない。対応確認済みfinding IDと元review IDを照合し、元threadを一括resolveする。old reviewをnew headの証拠に流用しない。

GitHub RESTのreview作成は`commit_id`、`path`、`line`、`side`、rangeを受け取る。GraphQLの`resolveReviewThread`はthread IDを解決する。実行identityがPR authorと同じ場合、`APPROVE`や`REQUEST_CHANGES`を独立reviewerの判断として偽装せず、常に`COMMENT`とprovenanceを使う。現在の既存認証は`yomote`で、同じ`yomote`作成PRへの実配送を想定するため、この制約が適用される。

公式仕様:

- [Pull request reviews REST API](https://docs.github.com/en/rest/pulls/reviews)
- [Pull request review comments REST API](https://docs.github.com/en/rest/pulls/comments)
- [GraphQL `resolveReviewThread`](https://docs.github.com/en/graphql/reference/mutations#resolvereviewthread)

## 停止条件と限界

- head不一致、diff外path/line、不正side、100件を超えるpagination、重複receipt、別head receipt、認証拒否は停止する。
- publishまたはresolveの応答が不明なら再送しない。API予算とrate limitは[外部APIのレート予算](api-rate-budget.md)に従う。
- reviewerのGitHub identityを偽装しない。代行actor、独立reviewer ID、対象headを両方残す。
- 現在本当のfindingがないPRへ動作確認用のinlineを投稿しない。inlineはcontract testと次の実findingで確認し、それまでは実GitHub上のline thread作成を未検証と記録する。
