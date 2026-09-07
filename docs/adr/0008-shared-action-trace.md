# ADR 0008: 同じWorldの有限Event履歴をpollingで共有する

- 状態: 採用
- 対象: [Issue #22](https://github.com/yomote/agent-world/issues/22)

## 背景と決定

別タブではWorldStateだけが更新され、移動の原因となるEventを観察できなかった。
Simulatorに上限80件の揮発dequeを置き、成功とfailureを確定順に保持する。WorldStateの変更者はSimulatorだけとする。

GET `/api/events` はWorldと履歴を同じロック内で取得する。POSTも確定Eventに加え同じ時点のWorldと履歴を返す。UIは既存の1秒pollingを使い、直前のsnapshotのevent_idで重複を除く。world_idが変われば旧履歴と既読IDを捨てる。

## 理由とトレードオフ

Stateと履歴を別HTTP要求にすると、その間の移動や再起動で観測が食い違う。POSTにも履歴を含めることで、別タブのActionが先に確定した場合も確定順を保って取り込める。応答は最大80件分増えるが、カーソルや追加通信を設けず、保持メモリと応答件数を固定できる。

failureはrevisionを増やさないため、revisionだけを取得済み判定に使わない。サーバーから破棄されたEventは復元せず、取得間隔に80件を超えるEventが発生した場合は取りこぼし得る。DB、永続化、WebSocket、欠落のない配信保証は追加しない。通信結果不明はそのタブだけに残し、確定Eventとは区別する。
