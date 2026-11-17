"""Mock-only semantic adapter (M2 T4/T5, clarification C3).

Protocol-only semantic retrieval against an in-memory mock provider.
Deterministic, synthetic inputs only: no creds, no network, no socket /
requests / urllib / subprocess. Egress is disabled by default; live retrieval
is NOT implemented here (mock-only, live NOT EVALUATED in M2).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

# SSRF / egress control: disabled by default. Live retrieval requires explicit
# config and is not implemented in this mock-only module.
EGRESS_ENABLED = False
SEMANTIC_CONFIG: dict[str, Any] = {}

MOCK_PROVIDER = "mock"
DEFAULT_MODEL = "nemotron-3.5-super-vl:free (mock)"
PROMPT_VERSION = "m2-mock-v1"
COST_LIMIT = 1.0  # synthetic mock cost ceiling; never exceeded in protocol tests
MAX_RETRIES_DEFAULT = 3

# A citation is well-formed only when it carries structured reference fields.
# Free-form strings (e.g. bare "citation" text) are rejected: fail closed.
_CITATION_REQUIRED = frozenset({"source", "locator"})


def _is_live() -> bool:
    """Live mode only when explicitly opted into; never on by default."""
    return bool(SEMANTIC_CONFIG.get("provider")) and SEMANTIC_CONFIG.get("live") is True


@dataclass
class SemanticMockProvider:
    """In-memory mock provider. Synthetic inputs only; no credentials used."""

    name: str = MOCK_PROVIDER
    model: str = DEFAULT_MODEL
    prompt_version: str = PROMPT_VERSION
    cost: float = 0.0
    calls: list[dict[str, Any]] = field(default_factory=list)

    def retrieve(self, query: str) -> dict[str, Any]:
        """Deterministic synthetic retrieval for *query*. Zero network egress."""
        if not isinstance(query, str):
            raise TypeError("query must be a string")
        qhash = hashlib.sha256(query.encode("utf-8")).hexdigest()
        entry = {
            "provider": self.name,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "query": query,
            "query_hash": qhash,
            "hits": [
                {"id": f"mock-{qhash[:8]}", "score": 1.0,
                 "text": f"synthetic context for {query!r}"}
            ],
            "cost": 0.0,
            "within_cost_limit": True,
        }
        self.calls.append(entry)
        return entry


def mock_retrieve(provider: SemanticMockProvider, query: str) -> dict[str, Any]:
    """Mock semantic retrieval. Protocol only; never routes to a live model."""
    if _is_live():
        raise RuntimeError("live egress is not implemented in the mock-only adapter")
    return provider.retrieve(query)


def quarantine_comment(text: str) -> dict[str, Any]:
    """Treat untrusted comment text as inert data: quarantine it, never execute.

    Comment text is data only — it is never interpreted as an instruction,
    so prompt-injection style content is quarantined and marked unsafe.
    """
    if not isinstance(text, str):
        text = str(text)
    return {
        "quarantined": True,
        "safe": False,
        "source": "untrusted_comment",
        "text": text,
        "text_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "action": "quarantine",
    }


def validate_citation(citation: Any) -> dict[str, Any]:
    """Validate a citation against the structured citation schema.

    Fail closed: a citation must carry structured ``source`` and ``locator``
    fields; bare free-form strings (or missing fields) raise.
    """
    if not isinstance(citation, dict):
        raise ValueError("citation must be a mapping")
    missing = _CITATION_REQUIRED - set(citation.keys())
    if missing:
        raise ValueError(f"citation missing required fields: {sorted(missing)}")
    source = citation.get("source")
    locator = citation.get("locator")
    if not isinstance(source, str) or not source:
        raise ValueError("citation.source must be a non-empty string")
    if not isinstance(locator, str) or not locator:
        raise ValueError("citation.locator must be a non-empty string")
    return {
        "valid": True,
        "source": source,
        "locator": locator,
        "hash": hashlib.sha256(
            json.dumps(citation, sort_keys=True).encode("utf-8")
        ).hexdigest(),
    }


def record_semantic(provider: SemanticMockProvider, prompt: str) -> dict[str, Any]:
    """Record model / provider / prompt-version / input-hashes / cost metadata.

    Resource usage (cost) is recorded and checked against the cost limit.
    """
    if not isinstance(prompt, str):
        raise TypeError("prompt must be a string")
    phash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    cost = float(getattr(provider, "cost", 0.0))
    record = {
        "model": getattr(provider, "model", DEFAULT_MODEL),
        "provider": getattr(provider, "name", MOCK_PROVIDER),
        "prompt": prompt,
        "prompt_version": getattr(provider, "prompt_version", PROMPT_VERSION),
        "hashes": {
            "prompt": phash,
            "provider": hashlib.sha256(
                str(getattr(provider, "name", "")).encode("utf-8")
            ).hexdigest(),
        },
        "cost": cost,
        "cost_limit": COST_LIMIT,
        "within_cost_limit": cost <= COST_LIMIT,
    }
    return record


def mock_retrieve_with_retries(
    provider: SemanticMockProvider, query: str, max_retries: int = MAX_RETRIES_DEFAULT
) -> dict[str, Any]:
    """Bounded-retry mock retrieval; abstains once retries are exhausted."""
    max_retries = max(0, int(max_retries))
    last_error: str | None = None
    for _attempt in range(max_retries + 1):
        try:
            return mock_retrieve(provider, query)
        except Exception as exc:  # retry boundary: bounded, then abstain
            last_error = str(exc)
    return {
        "abstained": True,
        "reason": "bounded retries exhausted",
        "attempts": max_retries + 1,
        "error": last_error,
    }
