# ADR 0006: 物流Scenarioとローカルcapability境界

- 状態: Accepted
- 日付: 2026-09-15

## 文脈

物流MVPでは、2倉庫・2店舗の固定条件で「トラック追加だけ」と「倉庫能力を共有した再配分」を比較する。計画Actor、採用者、配送実行者を分けながら、World Simulatorだけが在庫・処理能力・注文・トラックを更新する必要がある。

本番認証はscope外だが、Actorが自己申告したroleで操作を許可する設計は避ける。一方、ブラウザから送る識別子だけでは利用者本人を認証できない。

## 決定

- 既存A/moveと別の`LogisticsWorldState`を同じWorld serviceに置き、更新は`apps/world/simulator.py`の`LogisticsSimulator`だけが行う。
- Scenario再生成、計画採用、配送を明示APIにする。比較runは毎回、新しい`world_id`と同じ在庫・注文・能力・道路・期条件から始める。
- Actorはreadonly観測からversion付き計画を返すpure moduleとし、API clientやcapabilityをimportしない。役割方策は在庫配分、倉庫capacity割当、fleet割当、納期照合のpure関数を直列に呼び、各出力artifactを次の入力へ渡す。
- serverは`Principal { id, kind, roles }`を固定registryから解決し、単一policyでprincipal・action・resourceを判定する。humanはreset/accept、serviceは採用済みplan rowのdispatchだけを要求できる。
- supported runtimeでは`OperatorCapabilities`と`DispatchCapabilities`を分け、Actorへprincipal selectorやoperator capabilityを渡さない。
- `run_id → decision_id → plan_id/version → action_id → event_id → world_revision`をEventとartifactで追跡する。同じ`action_id`は同じ確定結果を返して二重消費を防ぐ。通信結果不明はclient側の`transport_unknown`として扱い、自動再送しない。

## セキュリティ境界

これは127.0.0.1で動かす教育用のprincipal選択型policyデモであり、本番認証ではない。`X-Local-Principal`はsimulation selectorにすぎず、trusted identityではない。任意のraw HTTP callerや敵対的Actorがheaderを偽装することは防がない。opaque tokenへ置換して安全に見せることもしない。

## 結果

UIから計画・採否・確定結果を区別でき、認可拒否とdomain failureを別のEventとして観察できる。Actorの提案は採用済みplan row、revision、在庫、能力、注文、トラック、道路、期限の検証を通らなければWorldへ反映されない。

React hostが呼ぶorchestratorはScenarioボタン1回でreset、提案、auto-accept、shipmentの直列dispatch、actual集計まで行う。独立した人間承認checkpointや、4つの独立Agent processは実装しない。

16/16への改善はcapacity-awareな再配分の効果を示す。このMVPだけでは、Agentを複数役割に分けたこと自体の優位性を証明しない。将来比較では、同じ情報と再配分能力を持つ単独方策と役割分離方式を同条件で評価する。
