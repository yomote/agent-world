# 物流MVP 着工packet

- 目的 / DoD: 2倉庫・2店舗の固定Scenarioで、同じ初期条件の「トラック3台・近接計画据置」4/16と「倉庫能力を共有する再配分」16/16をSimulator actualとして観察する。plan、採否、確定結果、相関、認可・failure・unknownの境界を表示する。
- owner / 統合: `logistics_implementation` worker。独立reviewはPM controllerが別reviewerへ依頼する。
- branch / 作業dir: `codex/logistics-mvp` / `C:\Users\omote\.codex\task-checkouts\agent-world-logistics-mvp`
- 編集境界: 新worktree内の物流World、API、Actor、UI、関連docs・ADR・テスト・生成API契約。既存A/moveを維持する。
- 依存: 旧実装head `d04bc801fc0f44802ddeba8e0b2b138e86a0c2ff`をbackup refへ保持し、2026-09-17の`origin/main` `232838c801ba5cd2805c784978acfda1a759d298`へ物流差分だけを移植した。元repoのdirty stateを取り込まない。
- user override: 既存owner移転やclaimではなく、独立並行のFront Desk開発体制として進めることをユーザーが承認。registry/claimは操作しない。
- 設計判断: [ADR 0012](../adr/0012-logistics-scenario-and-local-capabilities.md)。principal選択型policyデモとsupported runtime capability分離。本番認証は実装しない。
- 契約検査: Python OpenAPIが数値制約を`1.0`、JavaScript JSONが`1`と表す差を同じJSON数値として比較する。minimum/maximumの契約情報は維持する。
- 検証: 物流Simulator/API/Actor unit、既存全test、API生成、`npm run check`、実FastAPI+ViteのブラウザでA/B/Cと表示actualを確認する。
- review: 旧head `d04bc801`は独立reviewでblockingなし。現mainへの移植と設計文書を含む新headは、current check後に改めて独立reviewへ渡す。旧reviewを新headのPASSとして扱わない。
- 外部操作: ユーザーがIssueへの集約とDraft PRを承認済み。本文をlocal fileで確定し、独立review後に1回の意味あるpushとDraft PR作成を行う。mergeはしない。
- 停止条件: 既存変更との競合、ローカル権限拒否、検証結果不明。外部書込は再試行しない。

## 実装scope

固定Scenario、`dispatch_shipment`、plan採用、pure role pipeline、server policy、冪等性、相関、version/provenance、baseline比較に限定する。外部LLM、汎用orchestrator、永続DB、実配送、実費、本番認証は追加しない。

## 状態

2026-09-17: 旧base上の実装は独立reviewでblockingなし。レビューで固定handoff表示を実stage pipelineへ直し、run/decision相関、冪等性conflict、採否表示の境界を修正した。現`origin/main`へ物流専用差分を移植し、[将来設計](../proposals/agent-orchestration-domain-roadmap.md)と[物流rule土台](../proposals/logistics-rule-foundation.md)を正本化した。移植後candidate `ab3f7b124481651bfd52466277e6f2dbc65846b0`の全checkは完了したが、独立reviewで本packetの証跡区分にblocking指摘があり、この文書更新後headの再reviewと実画面確認、GitHub反映は未完了。

## ローカル証跡

### 現main移植後candidate `ab3f7b1`

- `npm run api:generate`: 生成成功。
- `npm run check`: API契約、lint、format、Vitest 28件、status dashboard 34件、Python 630件、TypeScript/Vite buildがpass。既知のPhaser chunk size warningと依存側deprecation warning 2件あり。checkは未commitの同一treeから開始し、実行中に内容を変えずcandidate `ab3f7b1`としてcommitした。dirty-startの結果であり、文書修正後headのcurrent checkとは区別する。
- 変更関連test: 物流/API Python 29件がpass。誤って実行した未定義script `npm run test:web`はscript-not-foundであり、test failureではない。Web testは正規の`npm run check`内Vitest 28件でpassした。
- 実画面: 未確認。旧headの画面結果を移植後candidateの結果へ読み替えない。
- 独立review: packetの旧head/current head証跡区分にblocking指摘。本小節への修正後headを再reviewするまで完了扱いにしない。

### 旧head `d04bc801`の履歴証跡

- `npm run check`: API契約、lint、format、frontend 10件、Python 47件、TypeScript/Vite buildがpass。
- 実画面: API 8010 / Vite 5174でA=4/16、B=4/16と`warehouse_capacity_exceeded`、C=16/16を確認。各runの別world_id、plan/handoff/Event/結果artifactの相関表示、既存A/moveの(3,2)→(4,2)とsuccess Traceも確認。
- 独立review: 相関、冪等性conflict、state不変を含めblockingなし。この結果は現main移植後headの受入証跡ではない。

### 実行環境と未完了

- 再現: APIを`python scripts/python_env.py -m uvicorn world.api:app --app-dir apps --host 127.0.0.1 --port 8010`、webを`AGENT_WORLD_API_PORT=8010`と`AGENT_WORLD_WEB_PORT=5174`を設定して`npm run dev:web`で起動する。通常の既定portは8000/5173。
- 現在: 独立reviewとユーザー試用のため、ローカルdev serverをAPI 8010 / frontend 5174で稼働中。永続hostingではない。停止は各起動terminalでCtrl+C。既存の別環境8000/5173は未変更。
- 未検証: 文書修正後headのcurrent check・独立review、現main移植後の実画面、GitHub CI、push/PR、production環境。本番認証はscope外。
- 未実装: AIによる次toolの自律選択、各役割のLLM化、動的な再計画loop、追加調査tool。現版はhostが固定順でpure rule pipelineを実行する。
- 学び: PythonがOpenAPI数値制約を`1.0`、JavaScriptが`1`と表す差は意味差ではない。API checkerでJSON数値として比較し、minimum/maximum契約を維持した。
