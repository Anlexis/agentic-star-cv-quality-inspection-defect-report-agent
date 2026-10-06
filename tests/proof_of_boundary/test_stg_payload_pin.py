# The committed smoke payload is the record the boundary suite proves end to end.
#
# The deployment evidence script sends deploy/invoke_payload.json to the running
# agent and reads the answer as the sign-off. If that payload drifted from the
# fixture the tests exercise, the deployment could be signed off on a request
# nobody had proven — or refused on one the tests never sent. Both files are
# READ here: the fixture from the boundary module, the payload from disk.

import json
from pathlib import Path

from tests.proof_of_boundary.test_pb_invoke_order import _VALID_PAYLOAD

_PAYLOAD_PATH = Path(__file__).resolve().parents[2] / "deploy" / "invoke_payload.json"


def test_smoke_payload_is_the_proven_fixture() -> None:
    payload = json.loads(_PAYLOAD_PATH.read_text(encoding="utf-8"))
    assert payload["input"] == _VALID_PAYLOAD
    assert isinstance(payload.get("session_id"), str) and payload["session_id"]


def test_smoke_payload_input_is_a_json_encoded_record() -> None:
    """The entry step JSON-parses `input`, so the payload must carry a JSON-encoded string."""
    payload = json.loads(_PAYLOAD_PATH.read_text(encoding="utf-8"))
    record = json.loads(payload["input"])
    assert record["defect_type"] == "crack"
