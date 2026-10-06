# Test Specification — MFG-C2-062

## Template Overview

**Template ID:** MFG-C2-062
**Name:** Manufacturing CV Quality Inspection Classification & Defect Report Agent
**Category:** Cat 2 (MFG — nested domain workflow)
**Outer graph:** `ManufacturingCVQualityInspectionAgent(AgentBaseGraph)`
**Inner graph:** `CVInspectionWorkflowGraph(BaseGraph)`

All tests are deterministic — no model, no network. The integration tests go through the real
ASGI application (`tests/integration/asgi.py`) with Bearer authentication at the trust level the
manifest declares, so what they prove is what a caller receives.

---

## Test Architecture

### Layers

| Layer | Path | Coverage focus |
|-------|------|---------------|
| Unit (contract) | `tests/unit/test_caller_contract.py` | The accepted request surface: finite/bounded numbers, inert rendered strings, structural caps, the disallowed-instruction screen (both directions), both request channels |
| Unit (execute-level) | `tests/unit/test_agent.py` | Each domain node's execute() contract; the shared confidence reader |
| Unit (framework compliance) | `tests/unit/test_framework_compliance_tc06_tc07.py` | S-2/S-3 default gates cannot be overridden |
| Integration (end to end) | `tests/integration/test_invoke_end_to_end.py` | Auth, real work from caller data on both channels, the context bridge, live runtime configuration, the structured-parameter screen, the refusal envelope |
| Integration (containment) | `tests/integration/test_error_envelope_containment.py` | A withheld report at the inner and the outer boundary; which layer refused; detector union |
| Integration (identity) | `tests/integration/test_manifest_identity_alignment.py` | The entrypoint provisions secrets under the identity the manifest declares — both sides read from files |
| Proof-of-Boundary | `tests/proof_of_boundary/test_pb_invoke_order.py` | Backbone order, S-1 trust gate, S-3 output gate |
| Proof-of-Boundary | `tests/proof_of_boundary/test_import_isolation.py` | No Level-0 imports |
| Proof-of-Boundary | `tests/proof_of_boundary/test_state_safety.py` | State msgpack-safety, no credentials |
| Proof-of-Boundary | `tests/proof_of_boundary/test_pb7_hitl_interrupt_propagation.py` | Skipped — `config/config.yaml` declares no `hitl.enabled` |

---

## Test Cases

### Caller contract (tests/unit/test_caller_contract.py)

| TC ID | Title | Input | Expected |
|-------|-------|-------|----------|
| TC-CC-001 | Non-finite numbers refused | `NaN`, `±Infinity`, `1e400`, booleans, non-numeric, out of range (as values and as strings) | `ContractError` naming the field; `code` names "finite"/"boolean"/"between" |
| TC-CC-002 | JSON non-finite literals refused | `{"coordinates": [NaN, 1]}` | refused on `input` |
| TC-CC-003 | Rendered strings inert | newline, CRLF, `;`, `[MASKED]`, markup, box characters, 33 chars, empty | refused; spelling variants of `no defect` normalise to `no-defect` |
| TC-CC-004 | Refusal never carries the value | forged `Decision` line in `defect_type` | message contains neither `ACCEPT` nor `Decision` |
| TC-CC-005 | Measurement parsed | `0.8mm`, `2.3 µm`, `12 cm` | value + unit; malformed/negative/overflowing/newline forms refused; absent allowed |
| TC-CC-006 | Timestamp validated | ISO 8601 forms | accepted; `yesterday`, invalid dates, newline, over-length refused |
| TC-CC-007 | Structural caps | 9 coordinates, 33+ fields, 8,001+ chars, non-object record, non-list coordinates | refused, field named |
| TC-CC-008 | Disallowed instructions | control tokens (`<\|…\|>`, `[INST]`, `<<SYS>>`, role tags), override/disclosure/role phrases incl. markup-spliced and zero-width forms, statement-level SQL/shell/script injection | refused |
| TC-CC-009 | Ordinary inspection text not flagged | documented plain-text formats incl. `;`, `--`, "rule", "prompt", "Operating System:" | accepted |
| TC-CC-010 | Structure screened depth-first, keys included | hostile key, nested value, `\u`-escaped token | refused |
| TC-CC-011 | Credential-shaped request refused | `AKIA…` in the request string | refused on `input`, value not in message |
| TC-CC-012 | Channels | structured record; JSON string; plain text | `record_source` = `input_context` / `input_json` / `input_text`; structured wins |
| TC-CC-013 | Plain-text extraction inert | unknown phrasing, 400-digit run | vocabulary-only output; no coordinate, no `inf` |
| TC-CC-014 | Absent optional fields | `{"coordinates": [1, 2]}` | `unknown` type/location, no measurement, empty timestamp |

### Unit Tests (tests/unit/test_agent.py)

#### DefectDescriptionParseNode

| TC ID | Title | Input | Expected |
|-------|-------|-------|----------|
| TC-DD-001 | JSON record published | validated contract from a JSON record | `parsed_defect` carries type, location, coordinates, parsed measurement, timestamp |
| TC-DD-002 | Plain-text record published | validated contract from plain text | `defect_type == "crack"`, measurement parsed |
| TC-DD-003 | No validated record | `validated_input` only, no contract | `status == ERROR`, no `parsed_defect` — never a re-parse of raw text |
| TC-DD-004 | Partial dict | valid contract | result keys are exactly `parsed_defect` + `status` |
| TC-DD-005 | Structured record published | contract from `input_context.defect` | `defect_type == "dent"` |

#### JISClassifyNode

| TC ID | Title | Input | Expected |
|-------|-------|-------|----------|
| TC-JIS-001 | Crack classification | `defect_type = "crack"` | `jis_class_code == "JIS-G-0303-S01"` |
| TC-JIS-002 | No-defect pass | `defect_type = "no-defect"` | `jis_class_code == "JIS-PASS"`, `confidence >= 0.90` |
| TC-JIS-003 | Unknown defect | `defect_type = "xyzzy_defect_type"` | `jis_class_code == "JIS-UNKNOWN"`, `confidence < 0.75` |
| TC-JIS-004 | Missing coordinates | Same defect type, coords=[] | `confidence` reduced by 0.10 vs with-coords |
| TC-JIS-005 | Valid confidence range | All supported defect types | `0.0 <= confidence_score <= 1.0` |

#### SeverityAssessNode

| TC ID | Title | Input | Expected |
|-------|-------|-------|----------|
| TC-SEV-001 | Surface crack Critical | `JIS-G-0303-S01`, `0.92` | `Critical` |
| TC-SEV-002 | Pass NoDefect | `JIS-PASS`, `0.95` | `NoDefect` |
| TC-SEV-003 | Low-confidence escalation | `JIS-B-0601-Rz` (Minor), `0.60` | `Major` (fail-safe) |
| TC-SEV-004 | Controlled vocabulary | All JIS codes | `severity_level in {Critical, Major, Minor, NoDefect}` |
| TC-SEV-005 | Unusable confidence fails closed | `JIS-PASS` with `NaN`, `±inf`, out of range, string, `None`, bool | `Minor` (escalated), never `NoDefect` |

#### RemediationLookupNode

| TC ID | Title | Input | Expected |
|-------|-------|-------|----------|
| TC-REM-001 | Known code | `JIS-G-0303-S01` | Non-empty substantive action text |
| TC-REM-002 | Pass code | `JIS-PASS` | Action contains PASS/ACCEPT/RELEASE |
| TC-REM-003 | Unknown code | `JIS-UNKNOWN` | Action contains QUARANTINE/MANUAL |
| TC-REM-004 | No credentials (S-5) | All JIS codes | The output gate's detector finds nothing in the action text |

#### ReportGenerateNode

| TC ID | Title | Input | Expected |
|-------|-------|-------|----------|
| TC-RPT-001 | Report generated | Full pipeline state | `defect_report` non-empty, length > 100 |
| TC-RPT-002 | Contains JIS code | `jis_class_code = "JIS-B-0601-Rz"` | report contains the code |
| TC-RPT-003 | Contains severity | `severity_level = "Critical"` | report contains "Critical" |
| TC-RPT-004 | Critical REJECT | `severity_level = "Critical"` | report contains "REJECT" |
| TC-RPT-005 | NoDefect ACCEPT | `NoDefect`, `0.95` | report contains "ACCEPT" |
| TC-RPT-006 | Rendered from validated parts | parsed measurement/coordinates/timestamp | `0.3 mm`, `(5.0, 10.0)`, ISO timestamp |
| TC-RPT-007 | A field outside the contract is never rendered | forged `Decision` line in each record field | "forged" absent; exactly one `Decision` line |
| TC-RPT-008 | Unusable confidence never accepts | `NoDefect` with `NaN`/`inf`/out of range/`None` | no ACCEPT; `Confidence : 0.0%`; no "nan" |

#### OutputGateNode

| TC ID | Title | Input | Expected |
|-------|-------|-------|----------|
| TC-OG-001 | High-conf Minor | `0.90`, `Minor` | `human_review_required == False` |
| TC-OG-002 | Low confidence | `0.60`, `Minor` | `human_review_required == True` |
| TC-OG-003 | Critical severity | `0.92`, `Critical` | `human_review_required == True` |
| TC-OG-004 | Result field | Report text provided | `result == defect_report` |
| TC-OG-005 | Credential-bearing report withheld | `api_key=…`, `password: …`, `AKIA…`, `glpat-…` | `status == ERROR`; `result` and `defect_report` both `None`; `human_review_required == True`; value absent |
| TC-OG-006 | Unusable confidence requires review | `NaN`, `inf`, out of range, `None`, string | `human_review_required == True` |

#### PreProcessNode

| TC ID | Title | Input | Expected |
|-------|-------|-------|----------|
| TC-PRE-001 | Valid input | JSON record + channel | `SUCCESS`; `caller_contract` published; `record_source == input_json` |
| TC-PRE-002/003 | Empty / whitespace | `""`, `"   "` | `ERROR`; `result is None`; truthy `formatted_output` |
| TC-PRE-004 | Non-string input | list / dict / int | `ERROR` without raising |
| TC-PRE-005 | Injection refused by the node itself | SQL, shell, script, override phrases, `<\|im_start\|>`, `[INST]`, `<<SYS>>`, markup-spliced directive | `ERROR`; no `caller_contract` |
| TC-PRE-006 | Legitimate inspection text accepted | documented JSON and plain-text formats incl. `;` | `SUCCESS` |
| TC-PRE-007 | Structured channel screened, keys included | hostile key under `defect` | `ERROR`; key not echoed |
| TC-PRE-008 | Refusal names the field, never the value | forged `Decision` in `defect_type` | `input.defect_type` in notice; "forged" absent |
| TC-PRE-009 | Masked value refused | `[MASKED]` as `defect_type` | `ERROR` naming `input.defect_type` |
| IMP-TRUST | Trust level | `PreProcessNode.required_trust_level` | `== TrustLevel.VERIFIED_EXTERNAL` |

#### Confidence reader

| TC ID | Title | Expected |
|-------|-------|----------|
| TC-CONF-001 | One reader shared by severity, gate and report | identity of `finite_confidence` in all three modules |
| TC-CONF-002 | Unusable values read as 0.0 | `NaN`, `±inf`, out of range, bool, string, `None`, list |
| TC-CONF-003 | Usable values pass through | 0, 0.5, 1, 0.749 |

---

### Integration — end to end (tests/integration/test_invoke_end_to_end.py)

| TC ID | Title | Description |
|-------|-------|-------------|
| E2E-001 | Health | `GET /health` → 200 |
| E2E-002 | Declared trust level | Bearer-authenticated JSON record → 200, `status == success` |
| E2E-003 | Backbone order | `node_history == [InitializeNode, PreProcessNode, CVInspectionWorkflowGraphNode, PostProcessNode, FinalizeNode]` |
| E2E-004 | Report computed from the record | Type/Location/Coordinates/Measurement/JIS Code/Level/Decision lines match the record; review notice present for Critical |
| E2E-005 | Different record, different report | scratch/edge → `JIS-B-0601-Rz`, Minor, REWORK, no notice |
| E2E-006 | Missing coordinates lower confidence and escalate | porosity with/without coordinates → Major vs Critical |
| E2E-007 | Documented plain-text format accepted | `Surface crack at (12.3, 45.6); width 0.8mm; NG` → success |
| E2E-008 | Unknown defect type → review | `gouge` → `JIS-UNKNOWN`, REJECT, notice |
| E2E-009 | Structured record reaches the inner graph | `input = "inspect"`, record only in `input_context.defect` → that record's report |
| E2E-010 | Structured record wins over the request string | both present → structured classified |
| E2E-011 | Structured channel not masked | title-case `Crack` on the structured channel → accepted as `crack` |
| E2E-012 | Missing / wrong credential refused | 401; identical detail either way |
| E2E-013 | `max_retry` is live | main slot faulted to RETRY once: `max_retry: 0` finalises after one attempt (pre_process ×1, not success); `max_retry: 3` re-runs pre_process (×2) and succeeds |
| E2E-014 | Config file is what the entrypoint loads | `runtime_config()["max_retry"] == 3` |
| E2E-015 | Invalid declared value refuses to start | `max_retry: -1` → `ConfigError` at compile |
| E2E-016 | Credential-shaped structured parameter refused | `AKIA…`, `sk-…`, connection string → 400 naming `input_context.note`; value never echoed |
| E2E-017 | Hostile field name reported by position | `input_context field #1` |
| E2E-018 | Undeclared field still screened | JWT under `extra` → 400 |
| E2E-019 | Ordinary text on the same field passes | `line_3_camera` → success |
| E2E-020 | Oversized parameters refused | 300 KB → 413 |
| E2E-021 | Screen equals the platform detector | `screen_input_context(ctx) is not None` ⇔ `bool(detect_credentials_in_value(ctx))` |
| E2E-022 | Refusal envelope | error status; field named; value, traceback, source path absent; pipeline not reached |
| E2E-023 | Masked value refused, not certified | title-case location in the request string → error naming `input.location`; mask token absent |
| E2E-024 | Disallowed forms refused by the template | `<<SYS>>`, `<\|system\|>`, JSON `NaN` → error before the pipeline |

### Integration — containment (tests/integration/test_error_envelope_containment.py)

The fault is injected on the DATA path: the report generator is made to emit a report carrying
a credential — one shape the platform recognises (`AKIA…`), one only the local patterns do
(`password=…`).

| TC ID | Title | Description |
|-------|-------|-------------|
| CT-001 | Inner output empty | inner graph invoke → `status == error`, `output` empty, value absent |
| CT-002 | Which layer refused | local shape: gate withheld (`human_review_required` True); platform shape: refused at the producing step by the platform's scan, gate never ran (flag absent) |
| CT-003 | Outer envelope contained | `status == error`; no leaked value, no report content, no traceback, no source path; post_process not reached |
| CT-004 | Gate covers the platform floor | every platform-recognised shape refused by `find_credential`; local shapes (`password=`, `api_key:`, `glpat-`, `ghp_`, `xoxb-`, PEM) refused; ordinary report text released |
| CT-005 | Clean-path control | the same request without the fault succeeds with the real report; the gate step runs |

### Integration — refusal envelope (tests/integration/test_refusal_envelope_closed_set.py)

The caller-visible refusal is the one thing `AgentBaseGraph.get_output()` publishes on a
pre_process ERROR, so it is held to a closed set. The fault is injected on the DATA path — the
`ContractError` the node catches is made to carry a recognisable detail from outside the declared
vocabulary — never on the envelope code itself.

| TC ID | Title | Description |
|-------|-------|-------------|
| RE-001 | Declared wording, every path | 23 refusal paths → `formatted_output` is the exact declared notice |
| RE-002 | Drawn from the declared sets | notice decomposes into `REFUSAL_PREFIX` + a `REFUSAL_FIELD_LABELS` member + a `REFUSAL_CODES` member that is not the unspecified code |
| RE-003 | Truthy, fallback closed | `formatted_output` truthy, `result is None` on every path |
| RE-004 | No caller value echoed | hostile values absent from every key and value of the returned mapping |
| RE-005 | Seeded detail contained (node) | seeded field + code appear nowhere in the returned mapping or `error_log`; the field collapses to `input_context.defect` |
| RE-006 | Seeded detail contained (/invoke) | absent from the response text and from every key and value of the JSON body; `output` is the declared notice; no traceback, no source path; post_process not reached |
| RE-007 | Real refusal end to end | `/invoke` publishes the declared notice for a real contract refusal |
| RE-008 | Clean-path control | the same request without the seed still succeeds with the real report |
| RE-009 | Credential-shaped field name withheld | a `glpat-…` key in `input_context` is reported by position, not quoted; an ordinary key is still named |

### Integration — identity (tests/integration/test_manifest_identity_alignment.py)

| TC ID | Title | Description |
|-------|-------|-------------|
| ID-001 | Manifest declares identity fields | `namespace`, `name`, `industry` present |
| ID-002 | Provisioned namespace matches the manifest | `agent._secrets_provider._namespace == manifest["namespace"]` |
| ID-003 | Provisioned agent name matches the manifest | `_agent_name == manifest["name"]` |
| ID-004 | Namespace is lower(industry) | pins the direction of alignment |
| ID-005 | Manifest entry point is the served class | `manifest["class"]` resolves to the compiled agent's class |

### Proof-of-Boundary Tests (tests/proof_of_boundary/test_pb_invoke_order.py)

| TC ID | Title | Description |
|-------|-------|-------------|
| PB6-001 | Backbone order SUCCESS | `invoke(VALID_PAYLOAD, ctx=VERIFIED_EXTERNAL)` — 5-node order, `status == SUCCESS` |
| PB6-002 | Output present | `result["output"]` non-empty |
| PB6-003 | ANONYMOUS rejected | ANONYMOUS context → `status == ERROR` |
| PB-S3-001 | Low confidence flags review | `confidence=0.60` — `human_review_required=True` |
| PB-S3-002 | Critical flags review | `Critical` — `human_review_required=True` |
| PB-S3-003 | Credential withheld | `api_key=SECRET` — `ERROR`, `result` and `defect_report` cleared, value absent |
| PB-S3-004 | High-conf NoDefect passes | `0.95`, `NoDefect` — `human_review_required=False` |

`deploy/invoke_payload.json` carries the PB-6 `_VALID_PAYLOAD` verbatim.

---

## Security Gate Verification

| Gate | Verified by | Expected result |
|------|-------------|----------------|
| S-1 (trust) | PB6-003, E2E-012 | ANONYMOUS caller returns ERROR; unauthenticated HTTP caller gets 401 |
| S-2 (input) | TC-CC-*, TC-PRE-005/007/009, E2E-016–E2E-024 | Hostile and malformed values refused by the template itself; legitimate text accepted |
| S-3 (output) | TC-OG-005, PB-S3-003, CT-001–CT-004 | Credential-bearing report withheld; both fields cleared; envelope contained |
| S-4 (audit) | `scripts/check_audit_trace.py src/` | a domain `emit_trace_event` in every `FunctionNode.execute()` |
| S-5 (secrets) | TC-REM-004, ID-002/003 | No credentials in built-in KB text; secrets scoped to the manifest identity |
