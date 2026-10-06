# CV Quality Inspection & Defect Report Agent

AI agent for classifying computer-vision quality inspection defects and producing defect reports, built with Agentic Star.

> **Category**: Cat 2 (a domain-specific pipeline for one job-to-be-done)
> **Industry**: Manufacturing
> **Template ID**: MFG-C2-062

## Overview

A computer-vision inspection station reports a defect — a crack on a surface,
a scratch along an edge, a void inside a casting — as a short record: what it
saw, where, and how large. Someone still has to turn that into a decision a
production line can act on, and the decision has to be written down in the
form a quality system audits.

This agent takes one inspection record and returns an ISO 9001 style
non-conformance record: the defect classified against the JIS (Japanese
Industrial Standards) defect classes it knows, a severity on a fixed scale
(Critical / Major / Minor / NoDefect), the recommended remediation for that
class, and a disposition (REJECT / HOLD / REWORK / ACCEPT). A classification
the agent is not confident in, a Critical finding, or a defect it cannot
classify is flagged for a qualified reviewer rather than certified.

The classification is deterministic — a built-in table, no model call — so the
same record always produces the same report. Every value the caller sends is
validated before it can influence the report: rendered fields are restricted to
an inert alphabet, numbers must be finite and in range, and a request that
fails a check is refused with the field named and the value never repeated.

This is an agent template built with the **AGENTIC STAR** development platform and the
**AgentCore Framework**. It is intended to be taken as a starting point: fork it, adapt it to
your own data and policies, and run it inside your own AGENTIC STAR deployment.

## Requirements

**This template does not run standalone.** It requires:

| Requirement | Notes |
|---|---|
| **AGENTIC STAR platform** | The agent connects to the platform at start-up. Without it, start-up fails immediately (see *Behaviour without the platform* below). Deployment guides and API documentation: [AGENTIC STAR Developers](https://developers.fd.agenticstar.tm.softbank.jp/) |
| **AgentCore Framework** (`agenticstar-agentcore`) | Installed from PyPI as a dependency. |
| Python | >=3.11 |

```bash
pip install -e .
```

### Behaviour without the platform

The framework is designed to run **only** on AGENTIC STAR. There is no fallback or degraded
mode. If the platform is unreachable or the SDK version does not match, the agent fails at graph
compile / start-up preflight rather than starting in a partially working state. This is
intentional — a half-running agent is worse than one that refuses to start.

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

Tests run without a platform connection. Running the agent itself does not.

## Sending a request

The inspection record belongs in `input_context.defect`. The platform rewrites
personal-data shapes out of the request string at every node boundary, and its
name heuristic reads title-case words as personal names, so a record embedded in
`input` can arrive with a field replaced by a mask token — which the agent then
refuses rather than classifies. Records sent as structured parameters arrive
intact.

```json
{
  "input": "inspect",
  "input_context": {
    "defect": {
      "defect_type": "crack",
      "location": "surface",
      "coordinates": [12.3, 45.6],
      "measurement": "0.8mm",
      "timestamp": "2026-06-29T10:00:00"
    }
  }
}
```

When no structured record is sent, `input` may carry the same record as a JSON
object, or a plain-text description as an inspection system emits it
(`Surface crack at (12.3, 45.6); width 0.8mm; NG`).

`defect_type` and `location` are codes of 1–32 lowercase letters, digits,
underscore or hyphen; the defect types the agent classifies are `crack`,
`scratch`, `dent`, `void`, `burr`, `delamination`, `inclusion`, `porosity`,
`corrosion` and `no-defect` — any other code is reported as unclassified and
routed to human review. `coordinates` holds at most 8 finite numbers;
`measurement` is a number followed by `mm`, `um` or `cm`; `timestamp` is an
ISO 8601 date or date-time. A refused request names the field that failed and
never repeats the value. The design specification under `docs/` carries the
full contract.

When the server is started with `INVOKE_AUTH_TOKEN` set, callers present it as
a Bearer token; the request then runs at the trust level the agent requires.

## Project Structure

```
src/          agent implementation (nodes, graphs, services, schemas)
tests/        unit, boundary and integration tests
config/       agent manifest and runtime parameters
deploy/       local deployment recipe and a smoke payload
docs/         design and test documentation
```

`docs/` holds the design specification (`02_design.md`) and the test
specification (`03_test_spec.md`).

## Customising

1. Adjust `config/config.yaml` for your own environment — the retry budget the
   backbone applies when a step asks for a retry.
2. Extend the JIS classification table, the severity map and the remediation
   knowledge base under `src/nodes/` with the defect classes your line inspects.
3. Extend the accepted record fields in `src/services/caller_contract.py` if
   your inspection system emits others; every rendered field stays restricted.
4. Re-run the test suite.

## License

MIT — see [LICENSE](LICENSE).

## Status of this repository

This template is published **as is**, by its individual author, under the MIT license. It carries
**no warranty and no support commitment**, and no organisation stands behind its behaviour or
fitness for any purpose. Issues and pull requests may or may not receive a response; that is at
the sole discretion of the repository owner.
