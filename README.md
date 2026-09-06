# AI Agent Sandbox

Agent → Action → World Change の因果を観察する、最初のVertical Slice。
React + Vite / Phaser 2D / Python + FastAPI。LLM、APIキー、Docker、クラウドサービスは不要。

## 起動

前提: **Node.js 22.13以上（推奨22 LTS）・npm・Python 3.11以上**。リポジトリ直下で:

```sh
python scripts/dev.py
```

初回はnpm依存と専用 `.venv` を自動セットアップするためネットワークが必要。
以後は同じコマンド（または `npm run dev`）で起動。
[Sandbox](http://127.0.0.1:5173) / [API仕様](http://127.0.0.1:8000/docs)。停止はCtrl+C。

## 観察する

1. グリッドのAは初期位置 `(3, 2)`。矢印ボタンでmoveを発行する。
2. Simulatorが許可した移動だけがPhaserと「確定位置」に反映される。
3. 下部のAction Traceに `A → move(dx=1, dy=0) → success` と移動前後・revisionが出る。
4. Aを端まで動かし、さらに外向きに移動すると `failure / Worldの境界外`。座標とrevisionは変わらない。
5. Random actorを開始すると約0.7秒ごとに同じAPIへActionを発行。停止ボタンで止まる。

## 構成と制約

- `apps/world`: immutableなWorldState / Entity / Action / Event、move規則、HTTP API。
- `apps/web`: React操作・Trace、Phaser描画、独立したrandom actor。
- `scripts`: 起動・API生成。品質チェックは `npm run check`。

WorldStateを変更するのはSimulatorだけ。UI・Actorには状態更新APIを渡さない。
Worldは単一プロセスのメモリ内で、再起動すると初期状態に戻る。
Traceはこのタブの直近80件。他タブのActionはStateのみ1秒ごとのpollingで反映される。
通信失敗は「結果不明」であり、success/failureを捏造せず自動再送もしない。

認証・永続化・複数worker・LLM・God Agent・マルチエージェント・文明・経済・戦闘は未実装。
ローカル開発用として127.0.0.1にbindする。

## 開発資料

- [進捗dashboard（Factory rollout Issues）](https://github.com/yomote/agent-world/issues?q=is%3Aissue+milestone%3A%22Factory+rollout%22)

- [Mind Inboxの移植計画・参照履歴](docs/porting-plan.md)
- [設計とAPI契約](docs/architecture.md) / [ADR](docs/adr/0001-authoritative-world.md)
- [起動・品質チェック・トラブルシュート](docs/runbooks/local-dev.md)
- [テスト方針](docs/testing.md) / [Agent向け規約](AGENTS.md)
