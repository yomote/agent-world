from uuid import uuid4

import pytest
from world.incident_models import (
    ApplyRequest,
    ApprovalRequest,
    ExecutionRequest,
    IncidentRunRequest,
    IncidentToolRequest,
    RecoveryChange,
    RecoveryProposal,
)
from world.simulator import IncidentSimulator


def proposal(sim, snapshot, *changes):
    refs = []
    for tool in ("observe_system", "inspect_queue"):
        result = sim.tool(
            str(snapshot.summary.run_id), IncidentToolRequest(tool=tool, args={"limit": 20})
        )
        refs.extend(item.ref for item in result.evidence)
    return RecoveryProposal(
        expected_revision=snapshot.summary.revision,
        diagnosis="Corroborated mismatch",
        evidence_refs=tuple(refs),
        changes=changes,
        rollback="Restore prior config",
    )


def execute(sim, initial, candidate):
    run_id = str(initial.summary.run_id)
    waiting = sim.request_execution(run_id, ExecutionRequest(action_id=uuid4(), proposal=candidate))
    approved = sim.approve(
        run_id,
        ApprovalRequest(
            action_id=uuid4(),
            proposal_hash=waiting.proposal_hash,
            expected_revision=waiting.summary.revision,
            approve=True,
        ),
    )
    action_id = uuid4()
    applied = sim.apply_approved(
        run_id,
        ApplyRequest(
            action_id=action_id,
            proposal_hash=waiting.proposal_hash,
            expected_revision=approved.summary.revision,
        ),
    )
    verify_id = uuid4()
    verified = sim.tool(
        run_id,
        IncidentToolRequest(
            tool="advance_and_verify",
            args={
                "ticks": 4,
                "proposal_hash": waiting.proposal_hash,
                "action_id": str(action_id),
                "verification_action_id": str(verify_id),
            },
        ),
    )
    return waiting, applied, verified, action_id, verify_id


@pytest.mark.parametrize(
    ("scenario", "changes"),
    [
        (
            1,
            (
                RecoveryChange(
                    operation="set_config", service="worker", key="subscription", value="east"
                ),
            ),
        ),
        (
            2,
            (
                RecoveryChange(
                    operation="set_config", service="carrier", key="route", value="spare"
                ),
            ),
        ),
        (
            3,
            (
                RecoveryChange(operation="select_deployment", service="worker", value="v2"),
                RecoveryChange(
                    operation="requeue",
                    service="queue",
                    order_ids=tuple(f"O-{n:03d}" for n in range(1, 13)),
                    expected_state="dead_letter",
                ),
            ),
        ),
    ],
)
def test_each_fault_requires_typed_change_and_real_ledger_recovery(scenario, changes):
    """万能fixや定数成功で、実ledgerを動かさず回復表示する回帰を防ぐ。"""
    sim = IncidentSimulator()
    initial = sim.reset(IncidentRunRequest(scenario_number=scenario))
    candidate = proposal(sim, initial, *changes)
    assert sim.validate(str(initial.summary.run_id), candidate).status == "valid"
    _, applied, verified, _, _ = execute(sim, initial, candidate)
    assert applied.status == "applied"
    assert verified.summary.completed == 14
    report = verified.evidence[0].data
    assert report["target_orders"] == report["terminal_orders"] == 14
    assert report["unique_instruction_ids"] == report["unique_idempotency_keys"] == 14
    assert report["duplicate_rejections"] == 0
    assert report["canaries_completed"] == 2


def test_unapproved_write_cannot_mutate_world():
    """未承認の変更がWorldを変える回帰を防ぐ。"""
    sim = IncidentSimulator()
    initial = sim.reset(IncidentRunRequest(scenario_number=1))
    candidate = proposal(
        sim,
        initial,
        RecoveryChange(operation="set_config", service="worker", key="subscription", value="east"),
    )
    validated = sim.validate(str(initial.summary.run_id), candidate)
    before = sim.observe(str(initial.summary.run_id))
    denied = sim.apply_approved(
        str(initial.summary.run_id),
        ApplyRequest(
            action_id=uuid4(),
            proposal_hash=validated.proposal_hash,
            expected_revision=before.summary.revision,
        ),
    )
    assert denied.status == "failure"
    assert sim.observe(str(initial.summary.run_id)) == before


def test_verification_replay_is_idempotent_and_scope_conflict_is_denied():
    """検証replayでtickやledgerを二重更新する回帰を防ぐ。"""
    sim = IncidentSimulator()
    initial = sim.reset(IncidentRunRequest(scenario_number=1))
    candidate = proposal(
        sim,
        initial,
        RecoveryChange(operation="set_config", service="worker", key="subscription", value="east"),
    )
    waiting, _, verified, action_id, verify_id = execute(sim, initial, candidate)
    run_id = str(initial.summary.run_id)
    after = sim.observe(run_id)
    verify_request = IncidentToolRequest(
        tool="advance_and_verify",
        args={
            "ticks": 4,
            "proposal_hash": waiting.proposal_hash,
            "action_id": str(action_id),
            "verification_action_id": str(verify_id),
        },
    )
    assert sim.tool(run_id, verify_request).evidence == verified.evidence
    assert sim.observe(run_id) == after
    conflict = sim.tool(
        run_id, verify_request.model_copy(update={"args": {**verify_request.args, "ticks": 5}})
    )
    assert conflict.status == "denied"


def test_fabricated_evidence_and_second_approval_consumption_are_rejected():
    """捏造refや同じ承認hashの再消費を許す回帰を防ぐ。"""
    sim = IncidentSimulator()
    initial = sim.reset(IncidentRunRequest(scenario_number=1))
    change = RecoveryChange(
        operation="set_config", service="worker", key="subscription", value="east"
    )
    fake = RecoveryProposal(
        expected_revision=1,
        diagnosis="guess",
        evidence_refs=("obs:made-up", "evt:0001"),
        changes=(change,),
        rollback="restore",
    )
    assert sim.validate(str(initial.summary.run_id), fake).violations == (
        "evidence_not_issued_for_run",
    )
    candidate = proposal(sim, initial, change)
    waiting, _, _, _, _ = execute(sim, initial, candidate)
    after = sim.observe(str(initial.summary.run_id))
    second = sim.apply_approved(
        str(initial.summary.run_id),
        ApplyRequest(
            action_id=uuid4(),
            proposal_hash=waiting.proposal_hash,
            expected_revision=after.summary.revision,
        ),
    )
    assert second.violations == ("invalid_state_transition",)


def test_public_projections_do_not_expose_evaluator_cause_labels():
    """真因キーや期待解が通常の観測projectionへ漏れる回帰を防ぐ。"""
    sim = IncidentSimulator()
    for number in range(1, 7):
        snapshot = sim.reset(IncidentRunRequest(scenario_number=number))
        outputs = [snapshot.model_dump_json()]
        for tool in (
            "observe_system",
            "query_events",
            "inspect_queue",
            "read_service_config",
            "list_changes",
            "search_knowledge",
            "probe_dependency",
        ):
            outputs.append(
                sim.tool(
                    str(snapshot.summary.run_id), IncidentToolRequest(tool=tool, args={})
                ).model_dump_json()
            )
        joined = "".join(outputs)
        assert '"cause"' not in joined
        assert "expected_solution" not in joined
        assert "case_schema_mismatch" not in joined
