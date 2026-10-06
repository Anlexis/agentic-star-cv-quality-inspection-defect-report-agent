# Containment of a withheld report, driven at both boundaries.
#
# The inner graph's output falls back from `result` to `defect_report`, and the
# outer envelope resolves its output as `formatted_output or result`, in both
# cases with no status check. So a gate that redacted and released, or that
# cleared one field and not the other, would still ship the report. Three
# properties are live here:
#
#   1. at the inner boundary, a withheld report leaves BOTH fields empty, so the
#      inner output carries nothing (this is the level at which the clearing is
#      falsifiable — the outer boundary re-raises an inner error before any
#      merge, so removing the clearing does not show there);
#   2. at the outer boundary, the caller receives an error status, no report
#      content, no matched credential, no traceback, no source path;
#   3. the gate refuses every shape the platform's own detector refuses — if it
#      did not, the platform would raise on the gate's return value and discard
#      the gate's whole update, clearing included.
#
# The fault is injected on the DATA path, never on the gate: the report
# generator is made to emit a credential-bearing report, exactly as a drifted
# template or an upstream value would. Patching the gate would test the patch,
# not the agent.
#
# Deterministic — no model, no network.

import os

import pytest

from tests.integration.asgi import Client

_TOKEN = "test-caller-token"
os.environ["INVOKE_AUTH_TOKEN"] = _TOKEN

from framework.security.credential_detector import detect_credentials  # noqa: E402

from src.api.server import app  # noqa: E402
from src.graph.context_bridge import set_caller_contract  # noqa: E402
from src.graph.domain_workflow_graph import CVInspectionWorkflowGraph  # noqa: E402
from src.nodes.output_gate_node import find_credential  # noqa: E402
from src.nodes.report_generate_node import ReportGenerateNode  # noqa: E402
from src.services.caller_contract import validate_request  # noqa: E402

client = Client(app)
AUTH = {"Authorization": f"Bearer {_TOKEN}"}

_JSON_RECORD = '{"defect_type": "crack", "location": "surface", "coordinates": [12.3, 45.6], "measurement": "0.8mm"}'

# One shape the platform's detector recognises and one only the local patterns
# do. Probing with only the first would leave the local patterns unproven;
# probing with only the second would leave the platform floor unproven.
_PLATFORM_SHAPE = "AKIAIOSFODNN7EXAMPLE"
_LOCAL_SHAPE = "password=hunter2-super-secret"


def _drifted_report(secret):
    def _execute(self, state):  # noqa: ANN001
        return {"defect_report": f"DEFECT REPORT\n  Note : upstream {secret}\n  Decision : ACCEPT", "status": "success"}

    return _execute


@pytest.fixture(params=[_PLATFORM_SHAPE, _LOCAL_SHAPE], ids=["platform_shape", "local_shape"])
def leaked(request, monkeypatch):
    """Make the DATA path emit a report the gate must withhold."""
    monkeypatch.setattr(ReportGenerateNode, "execute", _drifted_report(request.param))
    return request.param


def _invoke():
    return client.post("/invoke", json={"input": _JSON_RECORD}, headers=AUTH)


def _inner_output():
    """Drive the inner graph directly, the way the main slot does."""
    set_caller_contract(validate_request(_JSON_RECORD))
    graph = CVInspectionWorkflowGraph()
    graph.compile()
    return graph.invoke("inspect")


class TestInnerBoundaryWithholdsTheReport:
    def test_inner_output_is_empty(self, leaked):
        output = _inner_output()
        assert output["status"] == "error"
        assert not output["output"], output["output"]

    def test_inner_output_carries_no_leaked_value(self, leaked):
        assert leaked not in str(_inner_output())


class TestWhichLayerRefused:
    """The two shapes are refused by different layers, and both are observed.

    A platform-recognised shape never reaches the gate: the platform's own
    output scan raises on the PRODUCING step's return value, that step's whole
    update is discarded, and the run carries an error status from there — the
    wrapper then skips every later step, the gate included. So on that path
    the report never enters state at all.

    A shape only the local patterns recognise passes the platform's scan and
    reaches the gate, which withholds it. The human-review flag is the
    observable that the gate ran: it is set only by the gate's own update.
    """

    def test_local_shape_is_withheld_by_the_gate(self, monkeypatch):
        monkeypatch.setattr(ReportGenerateNode, "execute", _drifted_report(_LOCAL_SHAPE))
        output = _inner_output()
        assert output["status"] == "error"
        assert output["human_review_required"] is True

    def test_platform_shape_is_refused_at_the_producing_step(self, monkeypatch):
        monkeypatch.setattr(ReportGenerateNode, "execute", _drifted_report(_PLATFORM_SHAPE))
        output = _inner_output()
        assert output["status"] == "error"
        # The gate's update never happened: the flag it would have set is absent.
        assert output["human_review_required"] is False
        assert not output["output"]


class TestOuterEnvelopeIsContained:
    def test_status_is_error(self, leaked):
        assert _invoke().json()["status"] == "error"

    def test_envelope_carries_no_leaked_value(self, leaked):
        assert leaked not in _invoke().text

    def test_envelope_carries_no_report_content(self, leaked):
        assert "DEFECT REPORT" not in _invoke().text
        assert "ACCEPT" not in _invoke().text

    def test_envelope_carries_no_traceback_or_source_path(self, leaked):
        body = _invoke().text
        assert "Traceback" not in body
        assert "/src/" not in body

    def test_post_process_never_runs_on_a_withheld_report(self, leaked):
        assert "PostProcessNode" not in _invoke().json()["node_history"]


class TestGateCoversThePlatformFloor:
    """Everything the platform detector refuses, the gate refuses first."""

    @pytest.mark.parametrize(
        "value",
        [
            "AKIAIOSFODNN7EXAMPLE",
            "sk_live_" + "a" * 20,
            "sk-" + "b" * 24,
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc",
            "Bearer " + "c" * 20,
            "postgresql://db.internal.example:5432/inspection",
        ],
    )
    def test_platform_shapes_are_refused_by_the_gate(self, value):
        assert detect_credentials(value), "probe value must be one the platform recognises"
        assert find_credential(f"report {value} end") is not None

    @pytest.mark.parametrize(
        "value",
        [
            "password=hunter2",
            "api_key: abc123",
            "glpat-" + "d" * 20,
            "ghp_" + "e" * 36,
            "xoxb-" + "1234-5678-abcdefghij",
            "-----BEGIN RSA PRIVATE KEY-----",
        ],
    )
    def test_local_shapes_the_platform_lacks_are_still_refused(self, value):
        assert find_credential(f"report {value} end") is not None

    @pytest.mark.parametrize(
        "value",
        [
            "Surface crack at (12.3, 45.6); width 0.8 mm",
            "JIS-G-0303-S01 Critical REJECT — Quarantine",
            "token holder: the part bearer plate",
            "confidence 92.0%",
        ],
    )
    def test_ordinary_report_text_is_released(self, value):
        assert find_credential(value) is None


class TestCleanPathControl:
    """Without the drift the same request still produces its real report.

    A gate that refused everything would pass every assertion above; this is
    what stops that from counting as containment.
    """

    def test_same_request_succeeds_when_the_data_path_is_clean(self):
        response = _invoke()
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        assert "JIS-G-0303-S01" in response.json()["output"]

    def test_gate_step_runs_on_the_clean_path_too(self):
        output = _inner_output()
        assert output["status"] == "success"
        assert "OutputGateNode" in output["node_history"]
