# Template Design Specification — MFG-C2-062

## Position in AgentCore Architecture

- **Agent Class**: ManufacturingCVQualityInspectionAgent
- **L1 Base**: AgentBaseGraph (L1 direct inheritance — no L2 intermediary)
- **Three-Layer Separation**:
  - State: flat TypedDict composition (no Pydantic — msgpack incompatible)
  - Node: L1 inheritance (Template Method: `execute(self, state: AgentState) -> dict` override only)
  - Graph: Cat 2 nested — outer AgentBaseGraph + GraphNode in `main` slot → inner BaseGraph

| Layer | Class | Role |
|-------|-------|------|
| L1 Base (framework base class) | AgentBaseGraph — direct framework inheritance | Outer backbone |

## Architecture Overview

### Cat 2 Nested Pattern

The outer graph (`src/graph/graph.py`) inherits `AgentBaseGraph` directly and places a
`CVInspectionWorkflowGraphNode(GraphNode)` in the `main` slot. The inner domain logic lives
in `src/graph/domain_workflow_graph.py` which inherits `BaseGraph`.

```
Outer backbone (AgentBaseGraph):
  START → initialize → pre_process → main[CVInspectionWorkflowGraphNode] → post_process → finalize → END

Inner workflow (CVInspectionWorkflowGraph / BaseGraph):
  START → defect_desc_parse → jis_classify → severity_assess
        → remediation_lookup → report_generate → output_gate → END
```

### Node Configuration

| Slot | Node | Responsibility | Trust Level |
|------|------|---------------|-------------|
| pre_process | PreProcessNode | S-1 trust boundary; S-2 request contract — validates every caller field once (`src/services/caller_contract.py`) | VERIFIED_EXTERNAL |
| main | CVInspectionWorkflowGraphNode | GraphNode wrapper; bridges the validated contract to the inner graph; delegates to CVInspectionWorkflowGraph | ANONYMOUS |
| post_process | PostProcessNode | Shape final output; copy result → formatted_output; append the human-review notice | ANONYMOUS |
| inner: defect_desc_parse | DefectDescriptionParseNode | Publish the validated record as `parsed_defect`; refuses if no validated record reached the pipeline | ANONYMOUS |
| inner: jis_classify | JISClassifyNode | Classify defect per JIS standards; produce class_code + confidence | ANONYMOUS |
| inner: severity_assess | SeverityAssessNode | Map JIS class → Critical / Major / Minor / NoDefect; escalate under the confidence threshold | ANONYMOUS |
| inner: remediation_lookup | RemediationLookupNode | Return remediation action from built-in knowledge base | ANONYMOUS |
| inner: report_generate | ReportGenerateNode | Render the ISO9001-format defect report from validated parts | ANONYMOUS |
| inner: output_gate | OutputGateNode | S-3 output gate — withholds a credential-bearing report; flags low-confidence for human QC review | ANONYMOUS |

### Data Flow

```
user_input (request string) + input_context.defect (structured record)
  → [pre_process] validated_input, caller_contract (JSON), enriched_context
  → [context bridge] caller_contract seeded into the inner state
  → [defect_desc_parse] parsed_defect (structured dict)
  → [jis_classify] jis_class_code, jis_class_name, confidence_score
  → [severity_assess] severity_level
  → [remediation_lookup] remediation_action
  → [report_generate] defect_report
  → [output_gate] human_review_required, result (= defect_report, or withheld)
  → [post_process] formatted_output
  → [finalize] response_metadata, node_history
```

### Request contract

Everything a caller can send is validated in one module, `src/services/caller_contract.py`,
called once from the pre_process node. No node downstream re-parses raw request data.

Two request channels:

| Channel | Shape | Notes |
|---------|-------|-------|
| `input_context.defect` | object: `defect_type`, `location`, `coordinates`, `measurement`, `timestamp` (aliases `type`, `loc`, `coords`, `raw_measurement`, `inspection_timestamp`) | Authoritative when present. Not rewritten by the platform's personal-data masking. |
| `input` | the same record as a JSON object, or a plain-text description (`Surface crack at (12.3, 45.6); width 0.8mm; NG`) | Used only when no structured record was sent. The platform masks personal-data shapes here, including title-case words read as names; a masked field is refused for its shape, never classified. |

Rules that hold for every field:

| Field | Rule | Refusal |
|-------|------|---------|
| `defect_type`, `location` | inert code: 1–32 characters of `[a-z0-9_-]` after lower-casing; ordinary spaces collapse to `-` (`No Defect` → `no-defect`); a line break or control character is refused outright | field named, value never repeated |
| `coordinates` | list of at most 8 numbers, each finite and within ±1,000,000; booleans, `NaN`, `Infinity` and numeric strings outside the range refused | `…coordinates[i]` |
| `measurement` | `<number><unit>` with unit `mm`, `um`/`µm` or `cm`; the number finite in [0, 1,000,000]; parsed into value + unit and re-rendered, never echoed | `…measurement` |
| `timestamp` | ISO 8601 date or date-time, at most 32 characters | `…timestamp` |
| record | at most 32 fields; unknown fields ignored | `input_context.defect` / `input` |
| request string | at most 8,000 characters; a credential-shaped value refused; JSON `NaN`/`Infinity` literals refused | `input` |
| any string, keys included | chat-template control tokens screened as a class (`<|…|>`, `[INST]`, `<<SYS>>`, role tags), directive phrases screened raw and after markup/invisible-character stripping, statement-level SQL and shell injection | `input` / `input_context` |

Plain-text extraction emits only vocabulary words and parsed numbers; no caller substring is echoed.
An unrecognised `defect_type` is not an error: it classifies as `JIS-UNKNOWN`, Critical, human review.

The rendered report is line-delimited and every line is a labelled field, so a caller value that
carried a newline could manufacture a line of its own — including the `Decision` line. That is
why rendered strings are restricted rather than escaped, and why the report node re-renders
numbers from their parsed form.

### Context bridge

The framework hands a nested graph only the request string; `input_context` and the outer
state do not cross. The validated contract crosses on a ContextVar (`src/graph/context_bridge.py`):
`CVInspectionWorkflowGraphNode.extract_input()` sets it one step before the inner invoke and
`CVInspectionWorkflowGraph._extra_initial_state()` seeds it into the inner state as
`caller_contract`. The inner parse step refuses when the contract is absent rather than
re-deriving a record from the request text — a silent fallback would hide a broken bridge
behind a plausible answer. Proven end to end: a request whose string is `inspect` and whose
record is only in `input_context` produces a report for that record.

### Runtime configuration

`config/config.yaml` is loaded by the registry and, identically, by the standalone entry point
(`src/graph/graph.py: runtime_config()`), so a declared value is live in both deployments.

| Key | Reader | Effect |
|-----|--------|--------|
| `max_retry` | framework backbone (`AgentBaseGraph.route`) | when the main step reports RETRY, the request is re-run through pre_process up to this many times; validated at compile time |

`timeout_s` is not declared: nothing in this template reads it (the framework reads it only in
LLM, embedding and vector-store clients this agent never constructs), and a declared value with no
reader is a promise the configuration cannot keep.

### State Definition

| Field | Type | Purpose | Required |
|-------|------|---------|----------|
| validated_input | str | Screened request string from pre_process | pre_process |
| caller_contract | str (JSON) | The validated inspection record and its source channel | pre_process, seeded inner |
| enriched_context | dict | Channel + source metadata from pre_process | pre_process |
| parsed_defect | dict | Structured defect info (type, location, coordinates, measurement, timestamp) | inner |
| jis_class_code | str | JIS defect classification code (e.g. "JIS-B-0601-Rz") | inner |
| jis_class_name | str | Human-readable JIS class name | inner |
| confidence_score | float | Classification confidence 0.0–1.0 | inner |
| severity_level | str | Critical / Major / Minor / NoDefect | inner |
| remediation_action | str | Recommended corrective action | inner |
| defect_report | str | ISO9001-format defect report text (cleared when withheld) | inner |
| human_review_required | bool | True if confidence < threshold, Critical, unclassified, or withheld | inner |
| result | str | Final output text (mirrored from defect_report; cleared when withheld) | output_gate |
| formatted_output | str | Shaped output from post_process, or the refusal notice | post_process / pre_process |

**State Constraints (mandatory):**
- Flat TypedDict only (primitives + JSON-serializable types); structured values stored as JSON strings
- No JWT, API keys, credentials in State (checkpoint DB leakage)
- InvocationContext via `config["configurable"]` only (not in State)
- No Pydantic models, dataclass, arbitrary Python objects (msgpack incompatible)

## Framework Utilization

### Shared Components Used
- [x] InvocationContext (correlation_id, session_id, caller_trust_level) — outer backbone
- [x] S-1 / S-2: `PreProcessNode` owns the request contract; refusal enforced in the node itself, proven by calling `execute()` directly
- [x] S-3: `OutputGateNode` — framework credential detector as the floor plus local shapes; withholds, never redacts-and-releases
- [x] S-4: `emit_trace_event()` — one domain-specific event per `execute()` in every node (positional args); does NOT duplicate node_start / node_complete
- [x] S-5: No credentials in state; no hardcoded keys; secrets provisioned under the manifest identity (`namespace: mfg`)

### Security Gate Summary (S-1 through S-5)

| Gate | Node | Implementation |
|------|------|---------------|
| S-1 (trust) | PreProcessNode (VERIFIED_EXTERNAL) | External callers at `VERIFIED_EXTERNAL` minimum; the standalone adapter promotes a Bearer-authenticated caller to that level |
| S-2 (input) | PreProcessNode + adapter | Request contract above; the adapter additionally screens `input_context` for credential shapes with the framework detector and refuses with 400 naming the field |
| S-3 (output) | OutputGateNode | Framework `detect_credentials` floor + local patterns (`password=`, `api_key:`, GitLab/GitHub/Slack token prefixes, PEM blocks); a hit returns ERROR and clears `result` and `defect_report`; `human_review_required=True` for low confidence |
| S-4 (audit) | All domain nodes | `emit_trace_event(event, payload, state)` — positional, never keyword; payloads carry field names and closed-set labels, never caller values |
| S-5 (secrets) | N/A | No external integrations; no credentials at runtime |

### Output gate and containment

The inner graph's output falls back from `result` to `defect_report`, and the outer envelope
resolves its output as `formatted_output or result` — both without a status check. So a gate
that redacted and released, or that cleared one field and not the other, would still ship the
report. On a credential hit the gate returns an error status and clears both fields.

Every non-success path of the outer graph was enumerated: on an inner error the main slot
re-raises (`error_strategy = "propagate"`), so `merge_output` never runs and outer `result` is
never written; the pre_process refusal writes `result = None` and a truthy `formatted_output`
notice; no other backbone step writes `result`. The outer envelope therefore has no error-path
`result` channel in this topology, and no extra envelope layer is added — one would contain a
leak on its own and make the gate's clearing unfalsifiable.

That refusal notice is itself a publication channel, so its contents are a **closed set**. It is
built from `REFUSAL_PREFIX` plus a field label from `REFUSAL_FIELD_LABELS` and a wording from
`REFUSAL_CODES`, all declared in `src/services/caller_contract.py`; `ContractError` forces
whatever it is handed into those sets at construction, and the node rebuilds the notice from the
exception's two attributes rather than interpolating the exception. A field path outside the
declared set collapses to the declared field it falls under, so a caller-supplied key never comes
back. The wording is unchanged by this: every raise site already passed a declared literal — the
change is that it is now enforced rather than observed. `error_log` keeps the same line as the
internal channel and is never projected.

Layers and what proves each:

| Layer | Falsified by |
|-------|--------------|
| gate clears both fields | inner-graph invoke with the report generator faulted to emit a locally-matched credential: `output` must be empty (removing the clearing releases `defect_report` through the fallback) |
| local patterns on top of the framework floor | the same fault with a `password=` shape: the framework does not match it, so only the local pattern withholds it — observed end to end |
| framework floor | a platform-recognised shape (`AKIA…`) is refused by the platform's own scan at the producing step, before the gate runs; the gate's own floor is exercised directly |

The detector is a union: wider is safe, narrower is a bypass (a value the platform catches and the
gate misses makes the platform raise inside the gate step and discard the clearing).

### Confidence reading

Three steps compare the confidence against a threshold — escalate the severity, require review,
accept the part. `NaN` compares False against every threshold, so an unchecked comparison would
take the permissive branch exactly when the score is meaningless. One reader
(`src/services/confidence.py`) is used by all three: anything that is not a finite number in
[0, 1] reads as 0.0, so every decision fails closed.

### Precision grid

Not applicable: the report renders no monetary aggregates, so there is no currency grid to
enforce. The invariant enforced instead is the report's own: severity from a controlled vocabulary,
exactly one `Decision` line, rendered codes from an inert alphabet, numbers re-rendered from parsed
values. JIS codes such as `JIS-G-0303-S01` and `JIS-B-0659-1-D` are exactly the letter-hyphen-digit
shape an unguarded numeric grammar would corrupt, which is a further reason not to import one.

### Platform masking

The platform's input filter masks personal-data shapes in the request string before any template
code runs, and its name heuristic reads title-case words (`Surface Panel Zone`) as personal
names. On the request-string channel a masked field arrives as the mask token and is refused for
its shape, naming the field. The structured channel (`input_context.defect`) is not rewritten and
is the documented route for records whose fields would otherwise be masked.

> **ADR-017 S-2/S-3 gate behaviour:**
> - `FunctionNode` subclass → `@final` gate runs automatically; extend via `_extra_security_gate_input()` / `_extra_security_gate_output()` only
> - `GraphNode` (`CVInspectionWorkflowGraphNode`) → deliberate no-op (inner graph nodes apply their own gates)
> - **MUST NOT override `_security_gate_input()` / `_security_gate_output()`** — `TypeError` at class definition

### Composition Pattern

- **Pattern**: Cat 2 Nested (GraphNode subgraph)
- **Outer graph**: `ManufacturingCVQualityInspectionAgent(AgentBaseGraph)` in `src/graph/graph.py`
- **Inner graph**: `CVInspectionWorkflowGraph(BaseGraph)` in `src/graph/domain_workflow_graph.py`
- **GraphNode**: `CVInspectionWorkflowGraphNode(GraphNode)` placed in `main` slot
- **Error propagation strategy**: propagate (inner SubgraphError surfaces to outer backbone)

## Import Isolation Confirmation
- [x] Template does not import agenticstar-platform SDK (Level 0)
- [x] Import targets: `framework.*` and `shared.*` only (no `agents.base.*` required)
- [x] L1 direct inheritance: `AgentBaseGraph`, `BaseGraph`, `FunctionNode`, `GraphNode`

## Design Decision Record

| Decision | Option A | Option B | Chosen | Rationale |
|----------|----------|----------|--------|-----------|
| L1 base type | AgentBaseGraph | AutonomousBaseGraph | AgentBaseGraph | Fixed multi-step pipeline — no autonomous loop needed |
| Composition pattern | Cat-1 flat slots | Cat-2 nested GraphNode | Cat-2 nested GraphNode | 6 domain steps exceed single-node responsibility; nested keeps backbone clean |
| Inner graph parent | BaseGraph | AgentBaseGraph | BaseGraph | Fully custom topology (linear) — no backbone needed inside inner graph |
| Request validation | per node, as needed | once, at the boundary | once, at the boundary (`caller_contract.py`) | one answer to "what is accepted?"; no node re-parses raw text |
| Rendered caller strings | escape | restrict to an inert alphabet | restrict | the report is line-delimited; a newline in a value is a forged line, and escaping would still print it |
| Structured record channel | request string only | `input_context.defect` + request string | both, structured authoritative | the platform masks title-case and personal-data shapes in the request string; the structured channel arrives intact |
| S-3 output gate on a hit | redact and release | withhold (error, both fields cleared) | withhold | redaction is not containment: the redacted report ships as a success, and one un-cleared field re-opens the fallback |
| Credential detector | framework only / local only | union | union | the framework misses `password=`; the local set misses `AKIA…`; either alone is a bypass |
| Uncertain classification | Auto-pass at threshold | Flag for human QC | Flag for human QC | never auto-certify 'no defect' under the confidence threshold; a non-finite confidence reads as fully uncertain |
| Severity mapping | Free-form string | Controlled vocabulary | Controlled vocabulary (Critical/Major/Minor/NoDefect) | ISO9001 audit requirement; prevents ambiguous reports |
| `timeout_s` in config | keep declared | drop | drop | no reader in this template; a dead declaration misdescribes the runtime |
