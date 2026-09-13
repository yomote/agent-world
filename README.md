# Agent World

ResidentのActionとWorldの変化を観察する、デモと実験の置場です。各デモは `demos/<name>/` にコード、固有資料、READMEをまとめます。新しい実験はまず独立したディレクトリとREADMEを追加し、複数デモで実需要が確認できてから共通runtimeを抽出します。

## デモ一覧

| デモ                                           | 状態    | 内容                                                                                             |
| ---------------------------------------------- | ------- | ------------------------------------------------------------------------------------------------ |
| [resident-move](demos/resident-move/README.md) | archive | Aがmoveを発行し、authoritativeなWorld Simulatorによる変化とfailureを観察する最初のVertical Slice |

archiveは削除予定という意味ではありません。再現できる実験資産として保守しますが、新機能開発の主対象からは外します。`docs/proposals/` に置かれた文書がある場合、それらは過去の未採用提案であり、現在の実装計画ではありません。

## 既存デモの起動

前提は **Node.js 22.13以上（推奨22 LTS）・npm・Python 3.11以上** です。リポジトリ直下で次を実行します。

```sh
python scripts/dev.py
```

初回はnpm依存と専用 `.venv` を自動セットアップするためネットワークが必要です。以後は同じコマンド、または `npm run dev` で起動できます。[画面](http://127.0.0.1:5173) / [API仕様](http://127.0.0.1:8000/docs)。停止はCtrl+Cです。

rootの `npm run dev:web`、`npm run dev:world`、`npm test`、`npm run build`、`npm run api:generate`、`npm run check` もresident-moveと共通の管理・検査を対象にしたまま維持します。rootの `Dockerfile` もresident-moveをbuildします。

## 開発資料

- [リポジトリ設計の入口](docs/architecture.md) / [配置判断](docs/adr/0011-demo-directory-layout.md)
- [管理statusのインフラ構成図](docs/assets/infrastructure/agent-world-infrastructure.svg)
- [起動・品質チェック・トラブルシュート](docs/runbooks/local-dev.md)
- [テスト方針](docs/testing.md) / [Agent向け規約](AGENTS.md)
- [Mind Inboxの移植計画・参照履歴](docs/porting-plan.md)
