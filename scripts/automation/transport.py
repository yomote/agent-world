"""runnerが予約した要求だけを既存connectorへ渡す、資格情報を持たないtransport。"""

import json
import time
from pathlib import Path
from uuid import uuid4


class Stop(RuntimeError):
    def __init__(self, state: str, reason: str):
        super().__init__(reason)
        self.state, self.reason = state, reason


def atomic_json(path: Path, value: dict):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=True, indent=2), encoding="utf-8")
    temporary.replace(path)


class Transport:
    """要求→実transport応答を同じ生存中dispatcherが消費する。後付けreceiptを拒否する。"""

    def __init__(self, campaign):
        self.campaign = campaign

    def call(self, operation: str, arguments: dict, *, write=False, seconds=90):
        task = self.campaign
        task.boundary()
        data = task.data()
        if data["connector_calls"] >= data["max_connector_calls"]:
            raise Stop("stopped", "connector_call_budget")
        data["connector_calls"] += 1
        identifier = str(uuid4())
        request = {
            "id": identifier,
            "campaign": task.name,
            "operation": operation,
            "arguments": arguments,
            "write": write,
        }
        with task.db:
            task.save(data)
            task.db.execute(
                "INSERT INTO delivery_operations(id,campaign,operation,state,request) "
                "VALUES (?,?,?,'inflight',?)",
                (identifier, task.name, operation, json.dumps(request)),
            )
        directory = task.directory / "transport"
        directory.mkdir(exist_ok=True)
        atomic_json(directory / "request.json", request)
        print(json.dumps({"transport_request": request}), flush=True)
        response_path = directory / (identifier + ".response.json")
        deadline = min(time.monotonic() + seconds, task.monotonic_deadline)
        while time.monotonic() < deadline:
            task.heartbeat()
            if response_path.exists():
                if response_path.stat().st_size > 4 * 1024 * 1024:
                    raise Stop("unknown", "transport_response_limit")
                response = json.loads(response_path.read_text(encoding="utf-8-sig"))
                if set(response) != {"id", "status", "result"} or response["id"] != identifier:
                    raise Stop("unknown", "transport_identity_mismatch")
                with task.db:
                    task.db.execute(
                        "UPDATE delivery_operations SET state=?,response=? WHERE id=?",
                        (response["status"], json.dumps(response), identifier),
                    )
                if response["status"] != "ok":
                    state = "approval_wait" if response["status"] == "approval_wait" else "unknown"
                    raise Stop(state, "transport_" + response["status"])
                return response["result"]
            time.sleep(0.25)
        raise Stop("unknown" if write else "stopped", "transport_timeout_no_replay")
