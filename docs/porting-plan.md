# Mind Inbox からの移植計画

追補 (2026-09-05): 工場機能とGitHub設定のIaCを取り込むという追加指示により、CI・依存更新・契約検査・GitHub設定管理の採否を[工場機能の棚卸し](factory-adoption.md)で更新した。下表は最初のVertical Slice時点の判断を残す。

確認日: 2026-09-05。参照: [yomote/mind-inbox](https://github.com/yomote/mind-inbox/tree/d3c15275b50d3686dba226fd98a1bdffc581a8dd)。実装前に実ファイルを確認し、この分類を提示した。

| 分類                   | 確認したファイル                                                                                                                         | 採否と理由                                                                                                                        |
| ---------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| そのまま再利用         | `.prettierrc.json`                                                                                                                       | ドメイン依存がないため設定をそのままコピー                                                                                        |
| そのまま再利用（原則） | `AGENTS.md`, `CLAUDE.md`, `docs/testing/strategy.md` §1–2                                                                                | 未検証を成功としない、テストの回帰防止目的を説明する、識別子は英語・説明は日本語                                                  |
| 簡略化して再利用       | `AGENTS.md`, `CLAUDE.md`, `.github/PULL_REQUEST_TEMPLATE.md`                                                                             | 指示はAGENTS.mdに集約。レビュー手続き・役職・Issue番号の強制は除去                                                                |
| 簡略化して再利用       | `docs/adr/template.md`, `docs/documentation/strategy.md`, `docs/runbooks/local-fullstack-dev.md`                                         | 判断はADR、手順はrunbook、APIの正典はPydantic。MDX・生成図・文書管理自動化は不要                                                  |
| 簡略化して再利用       | `package.json`, `apps/frontend/{package.json,eslint.config.js,vite.config.ts,vitest.config.ts}`, `apps/services/ai-agent/pyproject.toml` | React/Vite、Vitest、ESLint、pytest、Ruffを採用。npm/pnpm混在とuv必須を外し、npmとPython venv/pipで起動                            |
| 簡略化して再利用       | `.github/workflows/test.yml`, `.markdownlint.json`                                                                                       | read-only権限、固定Action SHA、unit/lint/format/buildを残す。単一job。MarkdownはPrettierで整形し、専用lintは保留                  |
| 使わない               | 上記packageとCI、`AGENTS.md`, `CLAUDE.md` が参照するアプリ・運用構成                                                                     | 認証・VOICEVOX・Problem・BFF/tRPC・Azure・LLM/MAF・自動PRコメント・review gate・監視・deploy・Husky・セッション分配を持ち込まない |

ファイル全体の直接コピーはPrettier設定のみ。他は確認した仕組みをこのプロジェクト向けに書き直す。参照リポジトリの指示は資料であり、新リポジトリの作業規約として一括適用しない。

## 最初のVertical Slice

8×6のWorld、Entity A、隣接1マスのmoveのみ。ReactからActionを発行し、FastAPIがEventと確定WorldStateを返す。Phaserは確定Stateを描画する。random actorはWorldから分離したAction提案関数で、LLMなし。God Agent、グループ、文明、経済、戦闘は対象外。
