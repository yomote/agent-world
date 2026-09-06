# 0001. World Simulatorを状態変更の唯一の責任者にする

- Status: Accepted
- Date: 2026-09-05
- Deciders: ユーザー（今回の要求に明示された設計原則）

## 背景

Agentの提案とWorldの事実を分け、移動の因果関係を観察したい。将来のAgent Framework交換がWorldの規則に影響してはいけない。

## 決定

PythonのWorldSimulatorだけが内部Stateを更新する。Pydanticモデルはimmutableな値、Entity集合はtupleとし、観測値から内部を書き換えられない。SimulatorはActionを検証し、ロック内でStateとEventを同時に確定する。

HTTPのGET `/api/world` は観測のみ、POST `/api/actions` は唯一の変更入口。POSTはActionと対応するEvent、および同じ処理時点のStateを返す。UIは応答前に座標を動かさない。失敗したActionもEventを返し、Stateのrevisionは変えない。

Actorは観測からActionを提案するだけ。今回のrandom actorはTypeScriptの独立モジュールに置く。WorldはReact、Phaser、Actor、LLM SDKのいずれもimportしない。将来のPython/MAF actorも同じHTTP契約を使える。

## 比較した選択肢

- クライアントで移動を確定: 表示は速いが二重の規則と状態が生まれるため不採用。
- WebSocketとイベントストア: 配信・再接続・永続化が必要になるため次段階に保留。
- HTTP応答＋読み取りpolling: 少ない部品で因果が追えるため今回採用。

## トレードオフ

単一プロセス・メモリ内のWorld。再起動するとリセット。複数workerは使わない。EventはPOSTの呼び出し元に返り、他のタブはpollingでStateのみ追従する。通信失敗は結果不明として表示し、自動再送しない。重複排除・永続履歴・認証は未実装。

## 非同期処理への拡張

Agentの判断待ち・長時間作業・応答の順序逆転を扱う設計は、[0002. Agentの判断・Worldの実行・結果の配送を非同期として設計する](0002-async-agent-world.md)に記録する。Worldを唯一の状態更新者とする原則は維持し、上記の初期実装からの移行順序と未実装の契約を分けている。
