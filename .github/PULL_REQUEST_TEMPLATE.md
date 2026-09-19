## 変更

何が困っていて、この変更でどう変わるか。

## 課題と状態

- Issue:
- owner / 状態:
- DoD:

## current head の証跡

### AC対応と作者review観点

- Issue責任者 / PMが管理するACへの対応（requirement ID / acceptance ID / source version / status / evidence）:
- 変更固有のreview観点（risk / business invariant / 確認点 / evidence / known unmet）:

作者の対応表は独立reviewerの確認範囲を制限せず、作者の自己申告だけで完了にしない。AC変更はIssue責任者 / PMが理由、履歴、影響をIssueへ記録する。PM受入とPO価値評価は独立review・CIと別に扱う。

- 対象head: `<40桁SHA>`
- 独立review: `<reviewer と結果>`
- 変更後の再確認: `<対象headで行った確認、または not_run>`

対象headが変わった場合、以前のreviewと検証は新しいheadの証跡ではない。PR本文またはコメントで最新headに対応する結果を残す。DraftのCI skipはPASSではない。

## 検証

- 実行した検査と結果（`pass` / `fail` / `not_run` / `skipped` / `unknown` を区別）:
- 防いだ回帰 (必要な場合):
- 未検証・残る制約:

## 受渡し時の振返り

- 学び: `なし` または再発防止が必要な学びと反映先

GitHub設定を変更する場合は、実環境のplan結果と適用後の再plan結果を区別して記録する。
