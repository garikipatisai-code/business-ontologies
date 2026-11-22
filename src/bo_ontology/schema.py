"""Typed business-ontology model for the deep-ontology slice.

Only the types used by the M2 vertical slice are defined here; the wider
Domain/Context/Capability taxonomy from the architecture is deliberately not
invented before it is used.

Epistemic origin and review status are separate, validated enums (Sec 8).
Invalid values raise ``ValueError`` (strict, no silent coercion).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class Origin(str, Enum):
    """Where an artifact came from (provenance Sec 8)."""

    DETERMINISTIC = "deterministic"
    MODEL_INFERRED = "model-inferred"
    HUMAN_REVIEWED = "human-reviewed"


class Status(str, Enum):
    """Epistemic status, independent of origin (provenance Sec 8)."""

    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    STALE = "stale"
    DISPUTED = "disputed"


class EventPhase(str, Enum):
    """Lifecycle phase of an event; construction is not publication (C2)."""

    DECLARATION = "declaration"
    CONSTRUCTION = "construction"
    PUBLICATION = "publication"
    CONSUMPTION = "consumption"


ORIGINS = frozenset(item.value for item in Origin)
STATUSES = frozenset(item.value for item in Status)
EVENT_PHASES = frozenset(item.value for item in EventPhase)


def _enum_value(value: Any, enum_type: type[Enum], label: str) -> str:
    """Return the enum value for *value*, or raise ``ValueError``."""
    if isinstance(value, enum_type):
        return value.value
    try:
        return enum_type(value).value
    except (TypeError, ValueError):
        allowed = ", ".join(sorted(item.value for item in enum_type))
        raise ValueError(
            f"invalid {label}: {value!r}; allowed: {allowed}"
        ) from None


def validate_origin(value: Any) -> str:
    """Validate and return a provenance origin string."""
    return _enum_value(value, Origin, "origin")


def validate_status(value: Any) -> str:
    """Validate and return a review status string."""
    return _enum_value(value, Status, "status")


def validate_event_phase(value: Any) -> str:
    """Validate and return an event phase string."""
    return _enum_value(value, EventPhase, "event phase")


def _jsonable(value: Any) -> Any:
    """Recursively convert enums/dataclasses to JSON-safe primitives."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _evidence_list(value: Any) -> list["Evidence"]:
    """Coerce evidence input (None, dict, Evidence, iterable) to a list."""
    if value is None:
        return []
    if isinstance(value, Evidence):
        return [value]
    if isinstance(value, dict):
        return [Evidence(**value)]
    return [item if isinstance(item, Evidence) else Evidence(**item) for item in value]


class _Model:
    """Small mixin: JSON-safe dict form for persistence and transport."""

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))


@dataclass
class Evidence(_Model):
    """Exact source location and extractor provenance for one artifact."""

    repo: str
    path: str
    commit: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    extractor: str | None = None
    version: str | None = None

    def __post_init__(self) -> None:
        if self.repo is None:
            raise ValueError("evidence repo is required")
        if not self.path:
            raise ValueError("evidence path is required")
        if self.line_start is not None and self.line_end is not None:
            if int(self.line_end) < int(self.line_start):
                raise ValueError("evidence line_end precedes line_start")

    @property
    def range(self) -> str:
        """Return ``path:start-end`` (or the bare path when no lines exist)."""
        if self.line_start is None:
            return self.path
        if self.line_end is None or self.line_end == self.line_start:
            return f"{self.path}:{self.line_start}"
        return f"{self.path}:{self.line_start}-{self.line_end}"

    # Extractor-facing aliases (deterministic.py uses file/line names).
    @property
    def file(self) -> str:
        return self.path

    @property
    def line(self) -> int | None:
        return self.line_start


@dataclass
class BusinessConcept(_Model):
    """A named business concept mentioned by the analysed repositories."""

    name: str
    description: str | None = None
    origin: str = Origin.DETERMINISTIC.value
    status: str = Status.PROPOSED.value
    confidence: float = 1.0
    evidence: list[Evidence] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.origin = validate_origin(self.origin)
        self.status = validate_status(self.status)
        self.evidence = _evidence_list(self.evidence)


@dataclass
class Operation(_Model):
    """An operation/action a business concept can perform."""

    name: str
    description: str | None = None
    origin: str = Origin.DETERMINISTIC.value
    status: str = Status.PROPOSED.value
    confidence: float = 1.0
    evidence: list[Evidence] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.origin = validate_origin(self.origin)
        self.status = validate_status(self.status)
        self.evidence = _evidence_list(self.evidence)


@dataclass
class PolicyParameter(_Model):
    """A configurable parameter referenced by a policy rule."""

    name: str
    value: Any = None
    type: str | None = None
    description: str | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("parameter name is required")


@dataclass
class State(_Model):
    """A business state that transitions can enter or leave."""

    name: str
    description: str | None = None
    origin: str = Origin.DETERMINISTIC.value
    status: str = Status.PROPOSED.value
    evidence: list[Evidence] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.origin = validate_origin(self.origin)
        self.status = validate_status(self.status)
        self.evidence = _evidence_list(self.evidence)


@dataclass
class Transition(_Model):
    """A guarded state transition.

    ``source`` is ``None`` when the source state is unknown; in that case
    ``unknown_source`` is ``True`` and no source is fabricated.
    """

    target: str
    source: str | None = None
    guard: str | None = None
    trigger: str | None = None
    evidence: list[Evidence] = field(default_factory=list)
    unknown_source: bool | None = None
    origin: str = Origin.DETERMINISTIC.value
    status: str = Status.PROPOSED.value

    def __post_init__(self) -> None:
        if not self.target:
            raise ValueError("transition target is required")
        if self.source is None:
            self.source = None
            if self.unknown_source is None:
                self.unknown_source = True
        elif self.unknown_source is None:
            self.unknown_source = False
        self.origin = validate_origin(self.origin)
        self.status = validate_status(self.status)
        self.evidence = _evidence_list(self.evidence)


@dataclass
class Rule(_Model):
    """A deep business rule with executable-shape context and evidence."""

    name: str
    value: Any = None
    statement: str | None = None
    preconditions: Any = field(default_factory=list)
    predicate: str | None = None
    params: Any = field(default_factory=list)
    exceptions: Any = field(default_factory=list)
    outcomes: Any = field(default_factory=list)
    location: Evidence | None = None
    supporting: Any = field(default_factory=list)
    conflicting: Any = field(default_factory=list)
    limitations: Any = field(default_factory=list)
    origin: str = Origin.DETERMINISTIC.value
    status: str = Status.PROPOSED.value
    confidence: float = 1.0
    evidence: list[Evidence] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("rule name is required")
        self.origin = validate_origin(self.origin)
        self.status = validate_status(self.status)
        self.evidence = _evidence_list(self.evidence)
        if isinstance(self.location, dict):
            self.location = Evidence(**self.location)
        elif self.location is not None and not isinstance(self.location, Evidence):
            raise ValueError("rule location must be Evidence or a mapping")


@dataclass
class Event(_Model):
    """A business event at one explicit lifecycle phase."""

    name: str
    phase: str = EventPhase.DECLARATION.value
    evidence: list[Evidence] = field(default_factory=list)
    origin: str = Origin.DETERMINISTIC.value
    status: str = Status.PROPOSED.value

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("event name is required")
        self.phase = validate_event_phase(self.phase)
        self.origin = validate_origin(self.origin)
        self.status = validate_status(self.status)
        self.evidence = _evidence_list(self.evidence)


@dataclass
class Assertion(_Model):
    """A publishable subject/predicate/object assertion.

    The id is content-addressed (stable across runs); ``occurrence`` counts
    repeated sightings. Origin (where it came from) and status (review state)
    are independent fields.
    """

    subject: str
    predicate: str
    object: Any
    occurrence: int = 1
    origin: str = Origin.DETERMINISTIC.value
    status: str = Status.PROPOSED.value
    confidence: float = 1.0
    evidence: list[Evidence] = field(default_factory=list)
    id: str | None = None

    def __post_init__(self) -> None:
        if not self.subject or not self.predicate:
            raise ValueError("assertion subject and predicate are required")
        self.origin = validate_origin(self.origin)
        self.status = validate_status(self.status)
        self.evidence = _evidence_list(self.evidence)
        if not isinstance(self.occurrence, int) or self.occurrence < 1:
            raise ValueError("assertion occurrence must be a positive integer")
        if self.id is None:
            self.id = self._stable_id()

    def _stable_id(self) -> str:
        payload = {
            "subject": self.subject,
            "predicate": self.predicate,
            "object": _jsonable(self.object),
            "evidence": [_jsonable(asdict(item)) for item in self.evidence],
        }
        encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @property
    def assertion_id(self) -> str:
        """Alias for the stable content-addressed id."""
        return self.id


@dataclass
class ReviewDecision(_Model):
    """One human review decision over an ontology assertion."""

    subject: str
    status: str = Status.PROPOSED.value
    reviewer: str | None = None
    reason: str | None = None
    version: int = 1
    original_status: str | None = None
    decided_at: str | None = None

    def __post_init__(self) -> None:
        if not self.subject:
            raise ValueError("review subject is required")
        self.status = validate_status(self.status)
        if self.original_status is not None:
            self.original_status = validate_status(self.original_status)
        if not isinstance(self.version, int) or self.version < 1:
            raise ValueError("review version must be a positive integer")


# Name used in review-lifecycle prose/tests.
Review = ReviewDecision
