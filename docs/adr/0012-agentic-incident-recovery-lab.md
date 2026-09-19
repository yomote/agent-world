# ADR 0012: 障害復旧Labは単一判断Agentと権威的Simulatorを分離する

- 状態: Proposed
- 日付: 2026-09-17

## 文脈

固定した役割名や画面上のhandoffでは、観測結果から次のtoolを選ぶAgentの価値を確認できない。実serviceを変更するデモは危険で、真因をpromptやfixture名から読める評価も成立しない。

## 決定

ローカルの注文・queue・worker・carrier・ledgerを `IncidentSimulator` で模し、状態更新を `apps/world/simulator.py` に限定する。Agent controllerは別processで動かし、公式 `codex exec` の構造化出力を「次tool / proposal / stop」の判断だけに使う。hostがallowlist toolを実行し、観測を次のmodel callへ渡す。任意shell・file・browser・app・環境変数はAgentへ渡さない。

変更はtyped diffを複製Worldで検証し、人間が同じproposal hashを承認した後だけ適用する。writeはaction IDとpayloadを結び、結果不明時は `lookup_action_status` で照合する。検証tickと一意canaryは承認hash・適用action IDへ束縛する。

同条件の決定的runbook baselineも公開toolだけを使う。比較はAgentの優越を前提にせず、品質・tool数・model call・人待ちを記録する。

## 隔離境界と限界

CLIは空の一時cwd、`--ephemeral --ignore-user-config --ignore-rules --sandbox read-only -a never` で起動し、shell/file相当のtool、browser、computer、apps、image、multi-agentを無効化し、環境継承をnoneにする。モデルへ渡すのはmission、予算、公開projection、tool schema、公開traceだけである。evaluatorのscenario manifest・期待解・fixture filesystem pathは渡さない。

これはCodex CLI自身のプロセス隔離を証明するsandboxではない。host側で直接利用できるtoolをゼロにした境界であり、OS-level container隔離ではない。本番認証、永続化、本番service接続、秘密管理は対象外である。

## 帰結

- 同じ症状で3原因と3 holdoutを動かせる。
- 実model接続なしのrunはAgent評価済みと扱わない。
- hidden chain-of-thoughtは保存せず、短い公開理由、tool入出力、source refs、差分、承認、検証artifactだけを保存する。
- holdoutを開封する前にAgent promptとbaselineをfreezeする。結果を見た後に調整したcaseは開発caseへ降格する。
