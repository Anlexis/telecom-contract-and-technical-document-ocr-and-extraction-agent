"""AgentCore Platform v1.0"""

# TEL-C2-002 — caller-input contract helpers.
#
# One module owns the rules that every caller-supplied value must satisfy, so
# the outer PreProcessNode and the inner InputValidateNode enforce exactly the
# same contract instead of two drifting copies.
#
# The rules, and why each one exists:
#
#   * Bounds — an OCR pipeline is fed whole scanned documents, so "how big can
#     this be" has to be an explicit number rather than whatever the caller
#     sends. Every bound below is a module constant and is asserted in tests.
#
#   * Finite numerics — `float("nan")` and `float("inf")` parse successfully and
#     then compare False against every threshold, so an unchecked numeric fails
#     OPEN on precisely the comparison it exists for. `finite_in_range()` is the
#     only way a caller-supplied number enters the pipeline.
#
#   * Inert metadata — `document_id`, `language` and `source` are echoed
#     verbatim into the extraction report. Free text there is caller-controlled
#     output, so they are locked to identifier shapes. `content` is deliberately
#     NOT locked: extracting its text is what this agent does.
#
#   * Control tokens — chat-template delimiters (`<|…|>`, `[INST]`, `<<SYS>>`)
#     are never legitimate text in a scanned telecom document, and a downstream
#     consumer may well feed the extraction report to a language model. They are
#     screened as a class, raw AND after markup stripping, over keys as well as
#     values, depth-first, so a `\u`-escaped or tag-spliced form cannot ride
#     through. Directive PHRASES are deliberately not screened here: a
#     regulatory filing may legitimately contain instruction-shaped prose, and
#     refusing it would block real work.
#
#   * Credential shapes — redacted, not refused. See `redact_credentials()`.
#
# Failure reasons are a closed set of constants. Nothing a caller supplied, and
# nothing an exception produced, is ever interpolated into a value that reaches
# the caller.

import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

from framework.security.credential_detector import detect_credentials

# ── Structural bounds ─────────────────────────────────────────────────────────

#: Largest accepted raw request body, in characters.
MAX_PAYLOAD_CHARS = 1_048_576
#: Largest accepted document body, in characters (~150 pages of dense OCR text).
MAX_CONTENT_CHARS = 400_000
#: Largest accepted number of top-level keys in the request object.
MAX_PAYLOAD_KEYS = 32
#: Largest number of segmented text blocks carried forward from one document.
MAX_TEXT_BLOCKS = 20_000
#: Accepted range for the declared page count.
MIN_PAGES = 0
MAX_PAGES = 100_000
#: Longest accepted inert metadata value.
MAX_IDENTIFIER_CHARS = 64

#: Reserved key carrying the redaction tally across the subgraph boundary.
#:
#: The inner graph is entered through `subgraph.invoke(user_input, …)`, which
#: builds a fresh initial state — no outer State field reaches it. The only
#: channel that crosses is the normalised request itself, so the tally travels
#: inside it under this reserved name. Any caller-supplied key starting with
#: `_` is dropped before it is set, so a caller cannot forge the tally.
REDACTION_KEY = "_redaction_counts"
RESERVED_KEY_PREFIX = "_"

# ── Redaction sentinels ───────────────────────────────────────────────────────

#: Written by the platform's S-2 filter when it masks a personal-data shape.
PLATFORM_MASK_SENTINEL = "[MASKED]"
#: Written by `redact_credentials()` below.
CREDENTIAL_SENTINEL = "[REDACTED_CREDENTIAL]"
REDACTION_SENTINELS: Tuple[str, ...] = (PLATFORM_MASK_SENTINEL, CREDENTIAL_SENTINEL)

# ── Closed-set failure reasons ────────────────────────────────────────────────
#
# These are the ONLY values that may reach the caller on a failure. They name a
# class of problem; they never carry a rejected value, an exception message, a
# file path or an upstream response body.

REASON_EMPTY_INPUT = "empty_input"
REASON_PAYLOAD_TOO_LARGE = "payload_too_large"
REASON_MALFORMED_JSON = "malformed_json"
REASON_NOT_AN_OBJECT = "request_must_be_a_json_object"
REASON_TOO_MANY_KEYS = "too_many_request_keys"
REASON_MISSING_FIELDS = "missing_required_fields"
REASON_EMPTY_CONTENT = "empty_document_content"
REASON_CONTENT_TOO_LARGE = "document_content_too_large"
REASON_INVALID_IDENTIFIER = "invalid_metadata_identifier"
REASON_INVALID_NUMBER = "invalid_numeric_field"
REASON_CONTROL_TOKEN = "control_token_in_request"
REASON_MISSING_DOCUMENT_DATA = "missing_document_data"
REASON_MISSING_EXTRACTED_FIELDS = "missing_extracted_fields"
REASON_OUTPUT_WITHHELD = "output_withheld_by_security_gate"
REASON_REQUEST_FAILED = "request_failed"

#: Every reason a caller may ever see. Tests assert the ERROR envelope draws
#: only from this set, which is what stops node-authored text leaking later.
FAILURE_REASONS = frozenset(
    {
        REASON_EMPTY_INPUT,
        REASON_PAYLOAD_TOO_LARGE,
        REASON_MALFORMED_JSON,
        REASON_NOT_AN_OBJECT,
        REASON_TOO_MANY_KEYS,
        REASON_MISSING_FIELDS,
        REASON_EMPTY_CONTENT,
        REASON_CONTENT_TOO_LARGE,
        REASON_INVALID_IDENTIFIER,
        REASON_INVALID_NUMBER,
        REASON_CONTROL_TOKEN,
        REASON_MISSING_DOCUMENT_DATA,
        REASON_MISSING_EXTRACTED_FIELDS,
        REASON_OUTPUT_WITHHELD,
        REASON_REQUEST_FAILED,
    }
)

# ── Inert metadata patterns ───────────────────────────────────────────────────
#
# Chosen against real telecom document identifiers (`TEL-DOC-20260712-042`,
# `SB-5G-2026-0042`), not against a generic lowercase-only class: a class that
# rejected this repo's own fixtures would simply block real work.

IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\-]{0,63}$")
LANGUAGE_RE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8}){0,2}$")

# ── Control tokens ────────────────────────────────────────────────────────────

CONTROL_TOKEN_RE = re.compile(
    r"<\|[^|>]{0,64}\|>"  # <|im_start|>, <|endoftext|>, …
    r"|\[/?INST\]"  # [INST] / [/INST]
    r"|<</?SYS>>",  # <<SYS>> / <</SYS>>
    re.IGNORECASE,
)

_MARKUP_RE = re.compile(r"<[^<>]{0,200}>")

# ── Credential patterns this template keeps ON TOP of the framework's ─────────
#
# `framework.security.credential_detector` is the FLOOR, never a replacement:
# it describes credential FORMATS (sk_live_, sk-, eyJ, AKIA, Bearer, db URIs)
# and matches none of the shapes below. Taking the union is the only safe
# direction — a local set that replaced the framework's would be a narrowing
# dressed up as a tightening.
#
# `bearer_token_short` widens the framework's 16-character Bearer rule down to
# 8, but requires the candidate to carry a digit or one of `._-`. Without that
# requirement the rule fires on ordinary telecom vocabulary — "5G NR bearer
# service", "default bearer services" — and this repo's own fixture contains
# exactly that phrase. A gate that refuses real documents is a fail-CLOSED
# defect, not extra safety.
LOCAL_CREDENTIAL_PATTERNS: List[Tuple[str, "re.Pattern[str]"]] = [
    ("api_key_prefixed", re.compile(r"(?:sk|pk|ak)-[A-Za-z0-9]{16,}")),
    ("bearer_token_short", re.compile(r"Bearer\s+(?=[A-Za-z0-9._\-]{8,})[A-Za-z0-9._\-]*[\d._\-][A-Za-z0-9._\-]*")),
    (
        "credential_assignment",
        re.compile(
            r"(?:password|passwd|secret|api[_-]?key|token|access[_-]?key|private[_-]?key)" r"\s*[:=]\s*\S{8,}",
            re.IGNORECASE,
        ),
    ),
]


def strip_markup(text: str) -> str:
    """Return *text* with simple angle-bracket markup removed.

    A sanitizer that strips markup can splice two halves of a directive back
    together (`<|im<b>_start|>`), so every screen runs over BOTH the raw text
    and this reassembled form. Stripping alone is never treated as a defence.
    """
    return _MARKUP_RE.sub("", text)


def _normalise(text: str) -> str:
    """NFKC-fold so full-width and compatibility forms cannot evade a screen."""
    return unicodedata.normalize("NFKC", text)


def find_control_tokens(value: Any, _depth: int = 0) -> List[str]:
    """Return the control-token KINDS found anywhere in *value*.

    Walks dicts and lists depth-first and screens KEYS as well as values, so a
    payload that hides a delimiter in a field name is caught too. Returns the
    matched token text lower-cased and de-duplicated; callers report the kind,
    never the surrounding caller text.
    """
    if _depth > 12:
        return []
    found: List[str] = []
    if isinstance(value, str):
        for form in (value, strip_markup(value)):
            candidate = _normalise(form)
            found.extend(m.group(0).lower() for m in CONTROL_TOKEN_RE.finditer(candidate))
    elif isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str):
                found.extend(find_control_tokens(key, _depth + 1))
            found.extend(find_control_tokens(item, _depth + 1))
    elif isinstance(value, (list, tuple)):
        for item in value:
            found.extend(find_control_tokens(item, _depth + 1))
    return sorted(set(found))


def finite_in_range(value: Any, low: float, high: float) -> Optional[float]:
    """Return *value* as a finite float within [low, high], else ``None``.

    Rejects booleans (``isinstance(True, int)`` is True in Python), non-numeric
    strings, and NaN / ±Infinity — the last of which parse through ``float()``
    and then compare False against every bound, which is a silent fail-OPEN.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except (TypeError, ValueError):
            return None
    if not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    if not low <= number <= high:
        return None
    return number


def is_inert_identifier(value: Any) -> bool:
    """True when *value* is a bounded identifier safe to echo into the report."""
    return isinstance(value, str) and bool(IDENTIFIER_RE.match(value))


def is_language_tag(value: Any) -> bool:
    """True when *value* is a bounded BCP-47-shaped language tag."""
    return isinstance(value, str) and bool(LANGUAGE_RE.match(value))


def _redact_string(text: str) -> Tuple[str, Dict[str, int]]:
    """Replace every credential match in *text* with the sentinel."""
    spans: List[Tuple[int, int, str]] = [(f["start"], f["end"], str(f["type"])) for f in detect_credentials(text)]
    for kind, pattern in LOCAL_CREDENTIAL_PATTERNS:
        spans.extend((m.start(), m.end(), kind) for m in pattern.finditer(text))
    if not spans:
        return text, {}

    spans.sort(key=lambda s: (s[0], -s[1]))
    merged: List[Tuple[int, int, str]] = []
    counts: Dict[str, int] = {}
    for start, end, kind in spans:
        if merged and start < merged[-1][1]:
            # Overlapping findings describe the same secret; keep the widest.
            if end > merged[-1][1]:
                merged[-1] = (merged[-1][0], end, merged[-1][2])
            continue
        merged.append((start, end, kind))
        counts[kind] = counts.get(kind, 0) + 1

    out: List[str] = []
    cursor = 0
    for start, end, _kind in merged:
        out.append(text[cursor:start])
        out.append(CREDENTIAL_SENTINEL)
        cursor = end
    out.append(text[cursor:])
    return "".join(out), counts


def redact_credentials(value: Any, _depth: int = 0) -> Tuple[Any, Dict[str, int]]:
    """Return *value* with credential shapes replaced by a sentinel, plus counts.

    Redaction rather than refusal is deliberate, and it is the only option that
    leaves the agent usable. The framework's `@final` S-3 gate scans every value
    of every node result, so a credential-shaped string anywhere in the request
    makes the FIRST node return `status=error` with a traceback and nothing the
    caller can act on. A router configuration sheet carrying `password = …` is
    an ordinary instance of a document type this agent explicitly accepts, so
    failing the whole request on it is a denial of service. Removing the secret
    from the text instead keeps the document processable, and the caller is told
    how many values were redacted and of what kind — never what they were.

    Counts are keyed by credential KIND. The matched text is never returned,
    logged, or emitted in an audit event.
    """
    if _depth > 12:
        return value, {}
    if isinstance(value, str):
        return _redact_string(value)
    if isinstance(value, dict):
        out_map: Dict[Any, Any] = {}
        totals: Dict[str, int] = {}
        for key, item in value.items():
            cleaned, counts = redact_credentials(item, _depth + 1)
            out_map[key] = cleaned
            for kind, n in counts.items():
                totals[kind] = totals.get(kind, 0) + n
        return out_map, totals
    if isinstance(value, list):
        out_list: List[Any] = []
        totals = {}
        for item in value:
            cleaned, counts = redact_credentials(item, _depth + 1)
            out_list.append(cleaned)
            for kind, n in counts.items():
                totals[kind] = totals.get(kind, 0) + n
        return out_list, totals
    return value, {}


def contains_redaction_sentinel(value: Any) -> bool:
    """True when *value* carries a platform mask or a credential sentinel.

    A redaction sentinel is NOT an extracted value. The platform's S-2 filter
    masks personal-data shapes in the caller's text before any template code
    runs, so `[MASKED]` arrives looking like ordinary content — and a scorer
    that treats it as ordinary content will certify it. Every extracted value is
    tested with this before it is scored.
    """
    if not isinstance(value, str):
        return False
    return any(sentinel in value for sentinel in REDACTION_SENTINELS)
