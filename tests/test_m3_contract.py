"""M3 A2 contract-resolver interface tests.

RED by design: src/bo_resolve does not exist yet, so the module-level import
below raises ModuleNotFoundError at collection time (contract resolver
missing). These tests specify the frozen interface once
bo_resolve.contract_resolver lands:

resolve(repo_paths) -> {
    "call_edges": [...],
    "event_schema_links": [...],
    "shared_table_links": [...],
}
every compatibility flag defaults compatible-not-equivalent;
shared table / matching schema / call edge NEVER implies equivalence
(assert no equivalentTo without a human-accepted mapping fixture);
negative: dynamic dispatch (repo-e CreditGatewayClient) yields no edge.
"""

from bo_resolve.contract_resolver import resolve  # noqa: F401  (module missing -> collection error RED)

REPO_PATHS = ["fixtures/repo-e"]


def _all_links(result):
    return (
        list(result.get("call_edges", []))
        + list(result.get("event_schema_links", []))
        + list(result.get("shared_table_links", []))
    )


def test_resolve_returns_call_edges_event_schema_links_shared_table_links():
    """resolve(repo_paths) returns call_edges + event_schema_links +
    shared_table_links (all lists)."""
    result = resolve(REPO_PATHS)
    assert set(result) >= {"call_edges", "event_schema_links", "shared_table_links"}
    assert isinstance(result["call_edges"], list)
    assert isinstance(result["event_schema_links"], list)
    assert isinstance(result["shared_table_links"], list)


def test_every_compatibility_flag_defaults_compatible_not_equivalent():
    """Every compatibility flag defaults compatible-not-equivalent."""
    result = resolve(REPO_PATHS)
    for link in _all_links(result):
        flag = link.get("compatibility", link)
        compatible = flag.get("compatible")
        equivalent = flag.get("equivalent")
        assert compatible is True or compatible is None, link
        assert equivalent is False or equivalent is None, link


def test_shared_table_matching_schema_call_edge_never_implies_equivalence():
    """A shared table, matching event schema, or call edge never implies
    equivalence: no equivalentTo/equivalent without a human-accepted mapping."""
    result = resolve(REPO_PATHS)
    for link in _all_links(result):
        assert link.get("equivalentTo") is not True, link
        assert link.get("equivalent") is not True, link
        if link.get("mapping") == "equivalent":
            assert link.get("human_accepted") is True, link


def test_no_equivalent_to_without_human_accepted_mapping_fixture():
    """No equivalentTo/equivalent anywhere unless backed by a human-accepted
    mapping fixture (compatibility != equivalence)."""
    result = resolve(REPO_PATHS)
    equivalents = [
        link for link in _all_links(result)
        if link.get("equivalentTo") is True or link.get("equivalent") is True
    ]
    assert equivalents == [], equivalents


def test_dynamic_dispatch_yields_no_call_edge():
    """Negative: repo-e CreditGatewayClient.call (getattr dispatch) yields
    no call edge (abstain, no static invocation to resolve)."""
    result = resolve(REPO_PATHS)
    sources = {str(e.get("source")) for e in result.get("call_edges", [])}
    targets = {str(e.get("target")) for e in result.get("call_edges", [])}
    assert "CreditGatewayClient.call" not in sources, sources
    assert not any("CreditGateway.applyCredit" in t for t in targets), targets
