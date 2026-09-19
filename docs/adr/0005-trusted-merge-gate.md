# 0005. 信頼済みsourceからcurrent headをmergeする構造

- Status: Accepted
- Date: 2026-09-06

## 背景

reviewと検証をcurrent headへ結び付けても、保護されたmainへ統合する主体と信頼境界を明確にしなければ、PR由来コードを特権contextで実行したり、自己発行statusだけで統合したりする危険がある。native auto-mergeは条件変化後も有効状態が残り得るため、この用途の統合主体にしない。

## 決定

- merge判定を行うgateは、review済みのtrusted sourceからだけ読み込む。PR由来のworkflowやworktreeファイルを特権実行しない。
- WorldのSimulatorと同様に、mergeの確定はgateだけが行う責務として分離する。PR、CI、review comment、UIは根拠を提供するが、merge結果を先行確定しない。
- gateはcurrent PR headを対象にしたsquash mergeだけを許し、expected headをmerge APIへ渡す。これにより対象が変わった後の統合を防ぐ。
- merge結果、通信失敗、認可拒否、条件未充足は区別する。書き込み結果がunknownなら同じ操作を再送しない。
- formal local entryはtrusted sourceを固定git objectから読み、親tokenを受け取らない。identity、repository、source tree、proposal artifact、approval evidence、operation receiptを分離して検証する。World更新と同じく、提案・承認・確定を一つの入力に混ぜない。
- deployとmergeは別の責務である。local entryはpost-merge dispatchを持たず、deployを確定する権限を持たない。

## 代案と却下理由

- **PR由来workflowの`pull_request_target`実行**: 未reviewのPRコードへ特権を渡すため採用しない。
- **native auto-merge**: 条件変化後にも有効状態が残り得るため、current-head確認を伴うgateの代替にしない。
- **広い認証への自動fallback**: 可視性や認可が不足した条件を安全と誤認するため採用しない。
- **local entryをbootstrapとして自己導入**: mainに存在しないsourceを信頼済み入口とできないため採用しない。bootstrapは別の設計・承認対象である。

## 帰結とトレードオフ

trusted sourceとcurrent headを結び付けるため、統合は手順を踏み、認可や証跡が欠ける場合に止まる。これは速度より、どの主体が何を確定したかを追跡できることを優先する選択である。同一GitHub accountのcommentは人間の独立性を暗号学的に証明しない。この限界は運用上のtrusted operator assertionとして明示し、identity認証の完成と称しない。

実行条件、approval packet、CI待機、ruleset・thread・deploy確認、receiptの保存先、bootstrap例外、ownerと予算は[CIと外部アクセスの運用](../runbooks/ci.md#merge-gateとformal-local-entryの運用)を正本とする。
