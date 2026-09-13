# Agent Worldの構成

このリポジトリは、ResidentのActionとWorldの変化を観察する複数のデモや実験を `demos/` 配下に並べる。rootには共通の開発コマンド、依存定義、CI、infra、scripts、runbookを置く。デモ間で共有するSDKやruntimeは、具体的な複数の利用例が生まれた後に抽出する。

現在の実行対象はarchive状態の [resident-move](../demos/resident-move/README.md) である。詳しいWorld/API/UI契約は [デモ固有の設計](../demos/resident-move/docs/architecture.md) を参照する。

すべてのデモで、確定したWorldStateを更新できる主体を明示する。resident-moveでは `WorldSimulator` だけが更新し、UIとActorは観測とActionの発行だけを担う。World実装はActor、UI、LLM SDKへ依存させない。

配置の理由と移行方針は [ADR 0011](adr/0011-demo-directory-layout.md) に記録する。`docs/proposals/` に置かれた資料がある場合は未採用の過去提案であり、この入口やADRに反映されるまでは現在方針として扱わない。
