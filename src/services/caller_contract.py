"""AgentCore Platform v1.0"""

# Caller-request contract for the CV quality inspection agent.
#
# One place validates everything a caller can send, so there is exactly one
# answer to "what is accepted?" — the pre_process node calls into here and no
# node downstream re-parses raw request data.
#
# Two request channels reach this module:
#   * `input_context.defect`, the structured invocation parameter, which is
#     where an inspection record belongs. The platform rewrites personal-data
#     shapes out of the free-text request field at every node boundary, and its
#     name heuristic reads title-case proper nouns as personal names, so a
#     record embedded in the request string can arrive with fields replaced by a
#     mask token. This channel is not rewritten.
#   * the free-text request string (the `input` field), which may carry the
#     record as a JSON object, or a plain-text description as emitted by an
#     inspection system ("Surface crack at (12.3, 45.6); width 0.8mm"). Used
#     only when no structured record was sent.
#
# Rules that hold for every field:
#   * every string that renders into the released report is restricted to an
#     inert alphabet. The report is line-delimited, so caller free text there
#     is output injection: a value carrying a newline could manufacture a
#     "Decision :" line of its own;
#   * numbers go through a finite + bounded parser. NaN and the infinities
#     survive float() and compare False against every bound, so an unchecked
#     non-finite value silently passes the check it was meant to fail;
#   * structure is capped — the coordinate list, the record's field count,
#     the request length — so no caller can size the report;
#   * a value that fails any check REFUSES the request, naming the field but
#     never repeating the value. The refusal itself is a closed set — a label
#     from REFUSAL_FIELD_LABELS and a wording from REFUSAL_CODES, forced there
#     by ContractError — because the refusal notice is what the caller sees;
#   * a mask token the platform left behind ("[MASKED]") fails the inert
#     alphabet like any other free text, so a masked value can never be
#     classified as if it were an extracted one.

from __future__ import annotations

import json
import math
import re
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Tuple

from framework.security.credential_detector import detect_credentials

# ── Bounds ────────────────────────────────────────────────────────────────────
MAX_REQUEST_CHARS = 8000
MAX_RECORD_FIELDS = 32
MAX_COORDINATES = 8
MAX_CONTEXT_DEPTH = 6
MAX_TIMESTAMP_CHARS = 32

# Coordinates are sensor positions (pixels or millimetres); a measurement is a
# defect dimension. Both carry a finite, bounded range rather than accepting
# whatever float() will parse.
COORDINATE_MIN = -1_000_000.0
COORDINATE_MAX = 1_000_000.0
MEASUREMENT_MIN = 0.0
MEASUREMENT_MAX = 1_000_000.0

# ── Vocabularies ──────────────────────────────────────────────────────────────
# Defect types the plain-text extractor recognises. A structured record may
# carry any inert code; an unrecognised one classifies as JIS-UNKNOWN and is
# routed to human review, which is the documented behaviour for novel defects.
DEFECT_TYPE_VOCABULARY: Tuple[str, ...] = (
    "crack",
    "scratch",
    "dent",
    "void",
    "burr",
    "delamination",
    "inclusion",
    "porosity",
    "corrosion",
    "no-defect",
)
LOCATION_VOCABULARY: Tuple[str, ...] = (
    "surface",
    "edge",
    "corner",
    "center",
    "centre",
    "top",
    "bottom",
    "left",
    "right",
    "inner",
    "outer",
)
MEASUREMENT_UNITS: Tuple[str, ...] = ("mm", "um", "cm")

# ── Inert alphabets ───────────────────────────────────────────────────────────
# Rendered codes are restricted rather than escaped, so no caller string can
# open new structure in the rendered report.
_CODE_RE = re.compile(r"^[a-z0-9_-]{1,32}$")
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?)?$")
_MEASUREMENT_RE = re.compile(r"^([0-9]+(?:\.[0-9]+)?)\s*(mm|µm|um|cm)$", re.IGNORECASE)

# Zero-width and bidi controls: invisible in a rendered report, so they can
# hide a directive from a human reviewer while a model still reads it.
_INVISIBLE_RE = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]")
# Markup is stripped before the second screening pass, so a directive spliced
# with tags ("ig<b>nore</b> all previous instructions") is caught once the tags
# are gone — and control tokens are caught on the first pass, before the strip
# could remove them.
_MARKUP_RE = re.compile(r"<[^>]{0,64}>")

# ── Disallowed-instruction screen ─────────────────────────────────────────────
# Chat-template control tokens are screened as a CLASS, not as a list of the
# ones seen so far. They are how a payload forges a turn boundary, and they
# carry no meaning in an inspection record, so matching them cannot block real
# work. The platform's own screen scores <|im_start|> and [INST] but not
# <<SYS>> or <|system|>, so the class is covered here rather than assumed.
_CONTROL_TOKEN_PATTERNS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("chat_template_token", re.compile(r"<\|[^<>|]{0,64}\|>")),
    ("instruction_token", re.compile(r"\[/?INST\]", re.IGNORECASE)),
    ("system_token", re.compile(r"<</?SYS>>", re.IGNORECASE)),
    ("role_tag", re.compile(r"<\s*/?(?:system|assistant|user)\s*>", re.IGNORECASE)),
)

# Instruction-shaped phrases. Every pattern requires a verb AND its object, so
# the surrounding text has to actually be an instruction: a description whose
# words mention rules or a prompt does not match on its own.
_INSTRUCTION_PATTERNS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    (
        "instruction_override",
        re.compile(
            r"\b(?:ignore|disregard|forget|override)\b[\s\S]{0,40}?"
            r"\b(?:previous|prior|earlier|above|all)\b[\s\S]{0,20}?"
            r"\b(?:instruction|instructions|rule|rules|prompt|prompts|direction|directions)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "prompt_disclosure",
        re.compile(
            r"\b(?:reveal|show|print|repeat|output|dump)\b[\s\S]{0,30}?"
            r"\b(?:your|the)\b[\s\S]{0,20}?\b(?:system\s+prompt|instructions|rules)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "role_reassignment",
        re.compile(
            r"\byou\s+are\s+(?:now|from\s+now\s+on)\b[\s\S]{0,40}?\b(?:ai|assistant|model|dan|developer\s+mode)\b",
            re.IGNORECASE,
        ),
    ),
    # Statement-level SQL and shell injection. A bare ";" or "--" is ordinary
    # punctuation in an inspection description ("...; width 0.8mm; NG"), so the
    # metacharacter has to be followed by a statement or a command to count.
    (
        "sql_injection",
        re.compile(
            r"\bunion\s+(?:all\s+)?select\b|;\s*(?:drop|delete|insert|update|alter|truncate)\s+\w|\bxp_\w+",
            re.IGNORECASE,
        ),
    ),
    (
        "command_injection",
        re.compile(r"\$\([^)]{0,64}\)|`[^`]{1,64}`|(?:&&|\|\|)\s*(?:curl|wget|rm|sh|bash|nc|python)\b", re.IGNORECASE),
    ),
    ("script_injection", re.compile(r"<\s*script\b|javascript\s*:", re.IGNORECASE)),
)


def screen_disallowed_instructions(text: str) -> Optional[str]:
    """Name the first disallowed-instruction shape in a string, or None.

    Screened twice: once on the text as received, so control tokens are seen
    before any strip could remove them, and once with invisible characters and
    markup removed, so a directive spliced with tags is seen after the text
    re-assembles. A screen that only ran after the strip would silently convert
    a detectable token attack into undetectable plain text.
    """
    for name, pattern in _CONTROL_TOKEN_PATTERNS:
        if pattern.search(text):
            return name
    stripped = _MARKUP_RE.sub("", _INVISIBLE_RE.sub("", text))
    for candidate in (text, stripped):
        for name, pattern in _INSTRUCTION_PATTERNS:
            if pattern.search(candidate):
                return name
    return None


def _walk_strings(value: Any, depth: int = 0) -> List[str]:
    """Collect every string leaf AND every mapping key, depth-first.

    Keys are collected because a hostile field NAME is caller data on the same
    footing as its value, and a screen that read values only would pass it.
    """
    if depth > MAX_CONTEXT_DEPTH:
        return []
    if isinstance(value, str):
        return [value]
    found: List[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str):
                found.append(key)
            found.extend(_walk_strings(item, depth + 1))
    elif isinstance(value, (list, tuple)):
        for item in value:
            found.extend(_walk_strings(item, depth + 1))
    return found


def screen_structure(value: Any) -> Optional[str]:
    """Screen a parsed structure depth-first, keys included.

    Runs after parsing, so a payload that escaped its control tokens as \\u
    sequences is screened in its decoded form.
    """
    for text in _walk_strings(value):
        hit = screen_disallowed_instructions(text)
        if hit:
            return hit
    return None


# ── Refusal vocabulary ────────────────────────────────────────────────────────
# A refusal is a closed set on BOTH halves: REFUSAL_FIELD_LABELS holds every
# field path one may name, REFUSAL_CODES every wording one may carry. The
# caller-visible notice is REBUILT from those two sets rather than interpolated
# from whatever a raise site passed, because the notice is projected to the
# caller (AgentBaseGraph.get_output() returns formatted_output on ERROR).
#
# Every raise site in this module passes a literal today, so the set was closed
# by accident. Accident is not a contract: an inert-alphabet mask over a
# caller-supplied key would satisfy every raise site here and hand a caller's
# own text straight back out. Forcing both halves into the declared sets at
# construction makes the property structural, and the wording is unchanged.

#: Opening of the caller-visible refusal notice.
REFUSAL_PREFIX = "Request refused —"

#: Substituted for a code this module did not declare. A member of
#: REFUSAL_CODES itself, so the notice never leaves the closed set.
REFUSAL_CODE_UNSPECIFIED = "failed its contract check"

#: The request roots a refusal may name, and the record fields under them.
_REFUSAL_ROOTS: Tuple[str, ...] = ("input", "input_context", "input_context.defect")
_REFUSAL_LEAVES: Tuple[str, ...] = ("defect_type", "location", "coordinates", "measurement", "timestamp")
REFUSAL_FIELD_LABELS: frozenset[str] = frozenset(
    _REFUSAL_ROOTS + tuple(f"{root}.{leaf}" for root in _REFUSAL_ROOTS for leaf in _REFUSAL_LEAVES)
)

#: The numeric ranges this module declares, so their wording is enumerable.
_DECLARED_RANGES: Tuple[Tuple[float, float], ...] = (
    (COORDINATE_MIN, COORDINATE_MAX),
    (MEASUREMENT_MIN, MEASUREMENT_MAX),
)

#: The screen labels a refusal may quote — pattern NAMES, never matched text.
SCREEN_LABELS: Tuple[str, ...] = tuple(name for name, _ in _CONTROL_TOKEN_PATTERNS + _INSTRUCTION_PATTERNS)

REFUSAL_CODES: frozenset[str] = frozenset(
    (
        REFUSAL_CODE_UNSPECIFIED,
        "must be a non-empty string",
        f"must be at most {MAX_REQUEST_CHARS} characters",
        "contains a credential-shaped value",
        "is not a well-formed JSON inspection record",
        "must be an object",
        f"must hold at most {MAX_RECORD_FIELDS} fields",
        "must be a string",
        "must be 1-32 characters of lowercase letters, digits, underscore or hyphen",
        'must be a string such as "0.8mm"',
        "must be a number followed by a unit (mm, um or cm)",
        "must be an ISO-8601 date or date-time string",
        "must be a list of numbers",
        f"must hold at most {MAX_COORDINATES} values",
        "must be a number",
        "must be a number, not a boolean",
        "must be a finite number",
    )
    + tuple(f"must be between {low:g} and {high:g}" for low, high in _DECLARED_RANGES)
    + tuple(f"contains a disallowed instruction pattern ({label})" for label in SCREEN_LABELS)
)

# A coordinate is named by its position. The index is an integer this module
# bounded, not caller text, so it is re-attached after the base path is checked.
_INDEXED_FIELD_RE = re.compile(r"^(?P<base>[^\[\]]+)\[(?P<index>\d{1,3})\]$")


def refusal_field_label(field: object) -> str:
    """Collapse a field path to the declared label it falls under.

    A declared path is returned as it stands, with its bounded integer index
    where it has one. Anything else falls back to its nearest declared ancestor
    and, failing that, to the request root — so a caller-supplied key that
    reached a raise site is named by the field it sits under, never quoted.
    """
    text = field if isinstance(field, str) else ""
    suffix = ""
    match = _INDEXED_FIELD_RE.match(text)
    if match:
        text = match.group("base")
        suffix = f"[{int(match.group('index'))}]"
    if text in REFUSAL_FIELD_LABELS:
        return text + suffix
    while "." in text:
        text = text.rsplit(".", 1)[0]
        if text in REFUSAL_FIELD_LABELS:
            return text
    return "input"


def refusal_code(code: object) -> str:
    """Return *code* when this module declared it, else the unspecified code."""
    return code if isinstance(code, str) and code in REFUSAL_CODES else REFUSAL_CODE_UNSPECIFIED


def refusal_notice(field: object, code: object) -> str:
    """Build the caller-visible refusal from the two declared sets and nothing else."""
    return f"{REFUSAL_PREFIX} {refusal_field_label(field)}: {refusal_code(code)}"


# ── Validation error ──────────────────────────────────────────────────────────


class ContractError(ValueError):
    """A caller value failed its check. Carries closed-set labels, never a value.

    Two attributes, BOTH chosen by this module: `field` is a member of
    REFUSAL_FIELD_LABELS (a path outside it collapses to the declared field it
    falls under) and `code` is a member of REFUSAL_CODES. The rejected value is
    never part of either, and neither is a caller-supplied key — the normalising
    happens HERE, at construction, so no raise site can opt out of it.
    """

    def __init__(self, field: object, code: object) -> None:
        self.field = refusal_field_label(field)
        self.code = refusal_code(code)
        super().__init__(f"{self.field}: {self.code}")


# ── Scalar parsers ────────────────────────────────────────────────────────────


def finite_in_range(value: Any, field: str, low: float, high: float) -> float:
    """Parse a caller number, or refuse.

    Rejects booleans (isinstance(True, int) is True in Python, so an unchecked
    parser reads `true` as 1), non-numeric strings, NaN and both infinities, and
    magnitudes outside the declared range. NaN is the one that matters most:
    it parses through float() and then compares False against every bound, so a
    parser that only range-checked would let it through as "not out of range".
    """
    if isinstance(value, bool):
        raise ContractError(field, "must be a number, not a boolean")
    if isinstance(value, (int, float)):
        parsed = float(value)
    elif isinstance(value, str):
        try:
            parsed = float(value.strip())
        except (TypeError, ValueError):
            raise ContractError(field, "must be a number") from None
    else:
        raise ContractError(field, "must be a number")
    if not math.isfinite(parsed):
        raise ContractError(field, "must be a finite number")
    if not (low <= parsed <= high):
        raise ContractError(field, f"must be between {low:g} and {high:g}")
    return parsed


def inert_code(value: Any, field: str) -> str:
    """Accept a value that will be rendered into the report, or refuse.

    Lower-cased and space-normalised first, so "No Defect" and "no defect"
    both arrive as the vocabulary form "no-defect". Only ordinary spaces are
    collapsed. A newline, a tab or any other control character is NOT folded
    into a hyphen — a value carrying one is an attempt to open a new line in
    the rendered report, not a spelling variant — so it stays in the text and
    fails the alphabet check below like any other disallowed character. There
    is deliberately no separate control-character check: one alphabet, one
    refusal, nothing for a second layer to mask.
    """
    if not isinstance(value, str):
        raise ContractError(field, "must be a string")
    text = re.sub(r" +", "-", value.strip().lower())
    if not _CODE_RE.match(text):
        raise ContractError(field, "must be 1-32 characters of lowercase letters, digits, underscore or hyphen")
    return text


def parse_measurement(value: Any, field: str) -> Optional[Dict[str, Any]]:
    """Accept a defect dimension such as "0.8mm", or refuse.

    Returned as a parsed value and unit so the report renders it from validated
    parts rather than echoing the caller's string. An absent measurement is
    allowed — a record without one is still classifiable.
    """
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ContractError(field, 'must be a string such as "0.8mm"')
    match = _MEASUREMENT_RE.match(value.strip())
    if not match:
        raise ContractError(field, "must be a number followed by a unit (mm, um or cm)")
    magnitude = finite_in_range(match.group(1), field, MEASUREMENT_MIN, MEASUREMENT_MAX)
    unit = match.group(2).lower().replace("µm", "um")
    return {"value": magnitude, "unit": unit}


def is_timestamp(value: Any) -> bool:
    """True when the value is an ISO-8601 date or date-time within the length cap."""
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text or len(text) > MAX_TIMESTAMP_CHARS or not _TIMESTAMP_RE.match(text):
        return False
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def parse_timestamp(value: Any, field: str) -> str:
    """Accept an ISO-8601 date or date-time, or refuse. Absent is allowed."""
    if value in (None, ""):
        return ""
    if not is_timestamp(value):
        raise ContractError(field, "must be an ISO-8601 date or date-time string")
    return str(value).strip()


def parse_coordinates(value: Any, field: str) -> List[float]:
    """Accept a bounded list of finite coordinates, or refuse. Absent is allowed."""
    if value in (None, ""):
        return []
    if not isinstance(value, (list, tuple)):
        raise ContractError(field, "must be a list of numbers")
    if len(value) > MAX_COORDINATES:
        raise ContractError(field, f"must hold at most {MAX_COORDINATES} values")
    return [
        finite_in_range(item, f"{field}[{position}]", COORDINATE_MIN, COORDINATE_MAX)
        for position, item in enumerate(value)
    ]


# ── Record validation ─────────────────────────────────────────────────────────


def validate_record(raw: Any, where: str) -> Dict[str, Any]:
    """Validate one inspection record into its normalised, renderable form.

    Accepts the field aliases the inspection systems emit (`type`/`defect_type`,
    `loc`/`location`, `coords`/`coordinates`, `measurement`/`raw_measurement`,
    `timestamp`/`inspection_timestamp`). Keys outside that set are ignored,
    but the record's field count is capped so a caller cannot size the request.
    """
    if not isinstance(raw, Mapping):
        raise ContractError(where, "must be an object")
    if len(raw) > MAX_RECORD_FIELDS:
        raise ContractError(where, f"must hold at most {MAX_RECORD_FIELDS} fields")

    def pick(*names: str) -> Any:
        for name in names:
            if name in raw and raw[name] not in (None, ""):
                return raw[name]
        return None

    defect_raw = pick("defect_type", "type")
    defect_type = inert_code(defect_raw, f"{where}.defect_type") if defect_raw is not None else "unknown"
    location_raw = pick("location", "loc")
    location = inert_code(location_raw, f"{where}.location") if location_raw is not None else "unknown"

    return {
        "defect_type": defect_type,
        "location": location,
        "coordinates": parse_coordinates(pick("coordinates", "coords"), f"{where}.coordinates"),
        "measurement": parse_measurement(pick("measurement", "raw_measurement"), f"{where}.measurement"),
        "inspection_timestamp": parse_timestamp(pick("timestamp", "inspection_timestamp"), f"{where}.timestamp"),
    }


def _reject_non_finite_literal(name: str) -> float:
    """json.loads accepts NaN/Infinity literals by default; refuse them."""
    raise ValueError(f"non-finite JSON literal {name}")


def extract_plain_text(text: str) -> Dict[str, Any]:
    """Heuristic extraction from a free-text inspection description.

    Only vocabulary words and parsed numbers leave this function, so its output
    is inert by construction — no caller substring is echoed.
    """
    lowered = text.lower()
    defect_type = "unknown"
    for candidate in DEFECT_TYPE_VOCABULARY:
        if candidate in lowered or candidate.replace("-", " ") in lowered:
            defect_type = candidate
            break

    location = "unknown"
    for keyword in LOCATION_VOCABULARY:
        if keyword in lowered:
            location = keyword
            break

    coordinates: List[float] = []
    coord_match = re.search(
        r"\(\s*(-?[0-9]{1,12}(?:\.[0-9]{1,6})?)\s*[,\s]\s*(-?[0-9]{1,12}(?:\.[0-9]{1,6})?)\s*\)", text
    )
    if coord_match:
        coordinates = [
            finite_in_range(coord_match.group(1), "input.coordinates[0]", COORDINATE_MIN, COORDINATE_MAX),
            finite_in_range(coord_match.group(2), "input.coordinates[1]", COORDINATE_MIN, COORDINATE_MAX),
        ]

    measurement = None
    meas_match = re.search(r"([0-9]{1,12}(?:\.[0-9]{1,6})?)\s*(mm|µm|um|cm)\b", text, re.IGNORECASE)
    if meas_match:
        measurement = parse_measurement(meas_match.group(1) + meas_match.group(2), "input.measurement")

    return {
        "defect_type": defect_type,
        "location": location,
        "coordinates": coordinates,
        "measurement": measurement,
        "inspection_timestamp": "",
    }


# ── Request validation ────────────────────────────────────────────────────────


def validate_request(user_input: Any, input_context: Any = None) -> Dict[str, Any]:
    """Validate a whole request and return the contract the pipeline runs on.

    Raises ContractError naming the offending field. The caller's raw values
    never appear in the message and never leave this function except in
    validated, inert form.

    The contract carries the validated record and where it came from:
    `record_source` is "input_context" when the structured channel supplied it,
    "input_json" when the request string carried a JSON record, and
    "input_text" when it was extracted from a plain-text description.
    """
    if not isinstance(user_input, str) or not user_input.strip():
        raise ContractError("input", "must be a non-empty string")
    request_text = user_input.strip()
    if len(request_text) > MAX_REQUEST_CHARS:
        raise ContractError("input", f"must be at most {MAX_REQUEST_CHARS} characters")

    hit = screen_disallowed_instructions(request_text)
    if hit:
        raise ContractError("input", f"contains a disallowed instruction pattern ({hit})")

    # A credential-shaped value in the request string cannot succeed either way:
    # the platform's output gate scans every value of every node result, and the
    # screened request is one of them, so the run would fail at the request
    # boundary with nothing naming the cause. The SAME detector the platform's
    # gate calls is used, so what this refuses and what that blocks are one set.
    if detect_credentials(request_text):
        raise ContractError("input", "contains a credential-shaped value")

    context: Dict[str, Any] = dict(input_context) if isinstance(input_context, Mapping) else {}
    hit = screen_structure(context)
    if hit:
        raise ContractError("input_context", f"contains a disallowed instruction pattern ({hit})")

    structured = context.get("defect")
    if structured is not None:
        record = validate_record(structured, "input_context.defect")
        source = "input_context"
    elif request_text.startswith("{"):
        try:
            parsed = json.loads(request_text, parse_constant=_reject_non_finite_literal)
        except ValueError:
            raise ContractError("input", "is not a well-formed JSON inspection record") from None
        hit = screen_structure(parsed)
        if hit:
            raise ContractError("input", f"contains a disallowed instruction pattern ({hit})")
        record = validate_record(parsed, "input")
        source = "input_json"
    else:
        record = extract_plain_text(request_text)
        source = "input_text"

    return {"record": record, "record_source": source}
