# 物流MVP 着工packet

- 目的 / DoD: 2倉庫・2店舗の固定Scenarioで、同じ初期条件の「トラック3台・近接計画据置」4/16と「倉庫能力を共有する再配分」16/16をSimulator actualとして観察する。plan、採否、確定結果、相関、認可・failure・unknownの境界を表示する。
- owner / 統合: `logistics_implementation` worker。独立reviewはPM controllerが別reviewerへ依頼する。
- branch / 作業dir: `codex/logistics-mvp` / `C:\Users\omote\.codex\task-checkouts\agent-world-logistics-mvp`
- 編集境界: 新worktree内の物流World、API、Actor、UI、関連docs・ADR・テスト・生成API契約。既存A/moveを維持する。
- 依存: base `841a017759614ae2c3db82d4612e6b84351a0124`。元repoのdirty stateを取り込まない。
- user override: 既存owner移転やclaimではなく、独立並行のFront Desk開発体制として進めることをユーザーが承認。registry/claimは操作しない。
- 設計判断: [ADR 0006](../adr/0006-logistics-scenario-and-local-capabilities.md)。principal選択型policyデモとsupported runtime capability分離。本番認証は実装しない。
- 契約検査: Python OpenAPIが数値制約を`1.0`、JavaScript JSONが`1`と表す差を同じJSON数値として比較する。minimum/maximumの契約情報は維持する。
- 検証: 物流Simulator/API/Actor unit、既存全test、API生成、`npm run check`、実FastAPI+ViteのブラウザでA/B/Cと表示actualを確認する。
- review: 実装・検証後のcommit SHAをPMへ渡す。review待ちは完了扱いにしない。
- 外部操作: GitHub push、Issue/PR作成・更新は禁止。local commitはreview対象固定のため実施可。
- 停止条件: 既存変更との競合、ローカル権限拒否、検証結果不明。外部書込は再試行しない。

## 実装scope

固定Scenario、`dispatch_shipment`、plan採用、pure role pipeline、server policy、冪等性、相関、version/provenance、baseline比較に限定する。外部LLM、汎用orchestrator、永続DB、実配送、実費、本番認証は追加しない。

## 状態

2026-09-15: 実装とローカル検証を完了。独立worktreeを作成し、元repoのdirty stateとregistry/claimを未変更のまま保護した。独立review待ちで、全体受入・GitHub反映は未実施。

## ローカル証跡

- `npm run api:generate`: 生成成功。
- `npm run check`: API契約、lint、format、frontend 10件、Python 47件、TypeScript/Vite buildがpass。既知のPhaser chunk size warningと依存側deprecation warning 2件あり。
- 実画面: 既存port使用中のため、API 8010 / Vite 5174で確認。A=4/16、B=4/16と`warehouse_capacity_exceeded`、C=16/16。各runが別world_idであること、plan/handoff/Event/結果artifactの相関表示、既存A/moveの(3,2)→(4,2)とsuccess Traceを確認。
- 再現: APIを`python scripts/python_env.py -m uvicorn world.api:app --app-dir apps --host 127.0.0.1 --port 8010`、webを`AGENT_WORLD_API_PORT=8010`と`AGENT_WORLD_WEB_PORT=5174`を設定して`npm run dev:web`で起動する。通常の既定portは8000/5173。
- 現在: 独立reviewとユーザー試用のため、ローカルdev serverをAPI 8010 / frontend 5174で稼働中。永続hostingではない。停止は各起動terminalでCtrl+C。既存の別環境8000/5173は未変更。
- 未検証: 修正後の独立review、GitHub CI、push/PR、production環境。本番認証はscope外。
- 未実装: AIによる次toolの自律選択、各役割のLLM化、動的な再計画loop、追加調査tool。現版はhostが固定順でpure rule pipelineを実行する。
- 学び: PythonがOpenAPI数値制約を`1.0`、JavaScriptが`1`と表す差は意味差ではない。API checkerでJSON数値として比較し、minimum/maximum契約を維持した。
