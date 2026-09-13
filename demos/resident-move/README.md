# resident-move

Resident Aのmove Actionと、authoritativeなWorld Simulatorが確定するWorld変化を観察する最初のVertical Sliceです。このデモは **archive** 状態です。削除せず再現実験資産として保守しますが、新機能開発の主対象ではありません。

React + Vite / Phaser 2D / Python + FastAPIで構成し、LLM、APIキー、Docker、クラウドサービスは起動に不要です。

## 起動

リポジトリrootで次を実行します。

```sh
python scripts/dev.py
```

[画面](http://127.0.0.1:5173) / [API仕様](http://127.0.0.1:8000/docs)。個別起動は `npm run dev:world` と `npm run dev:web`、品質チェックは `npm run check` です。

## 観察する

1. グリッドのAは初期位置 `(3, 2)`。矢印ボタン、またはWorld領域を選択した後の矢印キーでmoveを発行する。長押し、修飾キー付き操作、入力欄では発行しない。
2. Simulatorが許可した移動だけがPhaserと確定位置に反映される。
3. Action TraceにAction、success/failure、移動前後、revisionが出る。
4. 境界の外へ動かすと `failure / Worldの境界外` となり、座標とrevisionは変わらない。
5. Random actorは同じAPIへActionを発行し、停止できる。
6. 同じWorldを別タブで開くと、確定位置と直近80件のAction Traceが約1秒ごとに反映される。

## 構成と制約

- `world/`: immutableなWorldState / Entity / Action / Event、move規則、HTTP API
- `web/`: React操作・Trace、Phaser描画、独立したrandom actor
- `docs/architecture.md`: このデモ固有の設計とAPI契約
- `docs/api/openapi.json` と `web/src/api/schema.d.ts`: `npm run api:generate` による生成物

WorldStateを変更するのは `world/simulator.py` のSimulatorだけです。UIとActorは観測とAction発行だけを行います。Worldは単一プロセスのメモリ内で、再起動すると新しいworld_idと初期状態になります。Simulatorは確定Eventを直近80件だけメモリに保持し、新しいタブにも共有します。通信失敗は結果不明としてそのタブだけに表示し、自動再送しません。

認証、永続化、複数World worker、LLM、God Agent、文明、経済、戦闘は未実装です。
