import json

from fastapi.testclient import TestClient
from incident_agent.controller import IncidentController
from incident_agent.models import ModelDecision
from incident_agent.provider import RunbookProvider, ScriptedProvider
from world.api import create_app
from world.local_auth import LabCapabilities


class LocalGateway:
    def __init__(self):
        self.caps = LabCapabilities("observer", "proposer", "operator", "executor")
        self.client = TestClient(create_app(capabilities=self.caps))

    def get(self, path):
        response = self.client.get(path, headers={"X-Lab-Capability": self.caps.observer})
        response.raise_for_status()
        return response.json()

    def post(self, path, payload):
        capability = self.caps.observer
        if path == "/api/incidents/runs" or path.endswith("/approval"):
            capability = self.caps.operator
        elif path.endswith("/apply"):
            capability = self.caps.executor
        elif "/proposals" in path:
            capability = self.caps.proposer
        elif path.endswith("/tools") and payload.get("tool") == "advance_and_verify":
            capability = self.caps.executor
        response = self.client.post(path, json=payload, headers={"X-Lab-Capability": capability})
        response.raise_for_status()
        return response.json()


def decision(kind, *, tool=None, args=None, proposal=None):
    return ModelDecision(
        kind=kind,
        next_tool=tool,
        args_json=json.dumps(args or {}),
        proposal_json=json.dumps(proposal) if proposal else None,
        short_public_reason=f"choose {tool or kind}",
        evidence_refs=[],
    )


def test_model_observation_tool_model_proposal_approval_verification_loop():
    """モデルが観測結果を受けず固定修正し、承認前後のloopを飛ばす回帰を防ぐ。"""
    gateway = LocalGateway()
    provider = ScriptedProvider(
        [
            decision("tool", tool="observe_system"),
            decision("tool", tool="inspect_queue", args={"limit": 20}),
            decision("tool", tool="read_service_config", args={"service": "worker"}),
            decision(
                "propose",
                proposal={
                    "expected_revision": 1,
                    "diagnosis": "Queue east differs from worker west",
                    "evidence_refs": ["obs:1:metrics:1", "obs:1:queue:2", "obs:1:config:3"],
                    "changes": [
                        {
                            "operation": "set_config",
                            "service": "worker",
                            "key": "subscription",
                            "value": "east",
                            "order_ids": [],
                            "expected_state": None,
                        }
                    ],
                    "rollback": "Restore west",
                },
            ),
            decision("tool", tool="advance_and_verify", args={"ticks": 4}),
        ]
    )
    controller = IncidentController(gateway, lambda mode: provider)
    run = controller.start(1)
    paused = controller.advance(run.run_id)
    assert paused.status == "waiting_approval"
    assert paused.model_calls == 4
    assert paused.tool_calls == 3
    assert paused.world["summary"]["completed"] == 0
    completed = controller.approve(run.run_id, True, "test-operator")
    assert completed.status == "recovered"
    assert completed.world["summary"]["completed"] == 14
    assert completed.world["summary"]["duplicate_count"] == 0
    assert [entry.name for entry in completed.trace if entry.kind == "model"] == [
        "tool",
        "tool",
        "tool",
        "propose",
        "tool",
    ]


def test_runbook_baseline_uses_public_tools_and_requires_human_tradeoff():
    """baselineが隠しcase manifestで分岐したり、業務判断を無断で決める回帰を防ぐ。"""
    controller = IncidentController(LocalGateway(), lambda mode: RunbookProvider())
    run = controller.start(2, "baseline")
    waiting = controller.advance(run.run_id)
    assert waiting.status == "waiting_human"
    assert waiting.model_calls == 0
    assert waiting.tool_calls == 7
    proposed = controller.answer(run.run_id, "use_spare")
    assert proposed.status == "waiting_approval"
    completed = controller.approve(run.run_id, True, "test-operator")
    assert completed.status == "recovered"


def test_unknown_model_result_stops_without_automatic_retry():
    """通信結果不明を成功扱いまたは無差別再送して外部call予算を消費する回帰を防ぐ。"""

    class FailingProvider:
        provider_name = "test"
        model_name = "test"
        calls = 0

        def decide(self, context):
            self.calls += 1
            raise RuntimeError("transport_unknown")

    provider = FailingProvider()
    controller = IncidentController(LocalGateway(), lambda mode: provider)
    run = controller.start(1)
    stopped = controller.advance(run.run_id)
    assert stopped.status == "unknown"
    assert provider.calls == 1
    assert stopped.model_calls == 1
    assert stopped.model_successes == 0
    assert stopped.model_failures == 1
    assert stopped.trace[-1].name == "runtime"


def test_lost_apply_response_is_reconciled_by_saved_action_id_without_replay():
    """apply応答喪失時にaction IDを失い、変更を無差別再送する回帰を防ぐ。"""
    base = LocalGateway()

    class LostApplyGateway:
        def get(self, path):
            return base.get(path)

        def post(self, path, payload):
            result = base.post(path, payload)
            if path.endswith("/apply"):
                raise RuntimeError("world_transport_unknown")
            return result

    provider = ScriptedProvider(
        [
            decision("tool", tool="observe_system"),
            decision("tool", tool="inspect_queue", args={"limit": 20}),
            decision("tool", tool="read_service_config", args={"service": "worker"}),
            decision(
                "propose",
                proposal={
                    "expected_revision": 1,
                    "diagnosis": "Queue east differs from worker west",
                    "evidence_refs": [
                        "obs:1:metrics:1",
                        "obs:1:queue:2",
                        "obs:1:config:3",
                    ],
                    "changes": [
                        {
                            "operation": "set_config",
                            "service": "worker",
                            "key": "subscription",
                            "value": "east",
                        }
                    ],
                    "rollback": "Restore west",
                },
            ),
        ]
    )
    controller = IncidentController(LostApplyGateway(), lambda mode: provider)
    started = controller.start(1)
    waiting = controller.advance(started.run_id)
    unknown = controller.approve(waiting.run_id, True, "ignored-client-name")
    assert unknown.status == "unknown"
    assert unknown.execution_action_id == unknown.reconciliation_action_id
    reconciled = controller.reconcile(waiting.run_id)
    assert reconciled.status == "verifying"
    assert reconciled.trace[-1].payload["status"] == "applied"
