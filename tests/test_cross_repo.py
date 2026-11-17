"""Cross-repo relation: repo-a invokes repo-b, with file+line both ends + provenance."""

import sqlite3
from pathlib import Path

from bo_extract.deterministic import extract
from bo_store.provenance import init_db, record_artifact

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"


def _get(obj, *keys, default=None):
    for key in keys:
        if isinstance(obj, dict) and key in obj and obj[key] is not None:
            return obj[key]
    return default


def _invocation(edges):
    matches = [
        e
        for e in edges
        if str(_get(e, "kind", "relation", "type", "")).lower() == "invokes"
        and _get(e, "source", "caller", "from") == "OrderService.createOrder"
        and _get(e, "target", "callee", "to") == "RefundPolicy.applyWindow"
    ]
    assert len(matches) == 1, f"expected exactly one cross-repo invocation, got {matches!r}"
    return matches[0]


def test_cross_repo_invocation_has_file_and_line_both_ends():
    nodes, edges, rules = extract(FIXTURES)
    rel = _invocation(edges)

    src_file = _get(rel, "source_file", "caller_file", "from_file")
    src_line = _get(rel, "source_line", "caller_line", "from_line")
    tgt_file = _get(rel, "target_file", "callee_file", "to_file")
    tgt_line = _get(rel, "target_line", "callee_line", "to_line")

    assert src_file and str(src_file).endswith("repo-a/app.py"), src_file
    assert src_line == 11, src_line
    assert tgt_file and str(tgt_file).endswith("repo-b/rules.py"), tgt_file
    assert tgt_line == 7, tgt_line
    assert str(_get(rel, "origin", "")).lower() == "deterministic"


def test_cross_repo_relation_persisted_with_provenance(tmp_path):
    nodes, edges, rules = extract(FIXTURES)
    rel = _invocation(edges)

    db = tmp_path / "prov.db"
    init_db(db)
    rid = record_artifact(
        db,
        repo=_get(rel, "source_repo", default="repo-a"),
        commit_sha="a" * 40,
        file_path=str(_get(rel, "source_file")),
        line_start=_get(rel, "source_line"),
        line_end=_get(rel, "source_line"),
        content_hash="c" * 64,
        extractor="deterministic",
        version="1.0",
        run_id="run1",
        timestamp="2026-01-01T00:00:00Z",
        origin="deterministic",
        status="proposed",
        confidence=1.0,
        basis="stdlib ast cross-repo call resolution",
        excerpt="OrderService.createOrder -> RefundPolicy.applyWindow",
    )
    assert isinstance(rid, int)

    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT repo, file_range, origin, status, confidence_basis, excerpt "
        "FROM provenance WHERE id = ?",
        (rid,),
    ).fetchone()
    assert row is not None
    repo, file_range, origin, status, confidence_basis, excerpt = row
    assert repo == "repo-a"
    assert "repo-a/app.py" in file_range
    assert "11" in file_range
    assert origin == "deterministic"
    assert status == "proposed"
    assert "stdlib ast" in str(confidence_basis).lower()
    assert "RefundPolicy.applyWindow" in excerpt


def test_extract_skips_symlinked_python_file(tmp_path):
    """A symlinked .py file is never listed/extracted (symlink escape guard)."""
    real = tmp_path / "real.py"
    real.write_text("class Real:\n    def run(self):\n        return 1\n")
    link = tmp_path / "link.py"
    link.symlink_to(real)

    nodes, _edges, _rules = extract(tmp_path)
    module_names = {n.get("name") for n in nodes if n.get("kind") == "module"}
    assert module_names == {"real"}, module_names
    assert "link" not in module_names


def test_extract_rejects_symlinked_dir_escape(tmp_path):
    """A .py symlink pointing outside the workspace root is not extracted."""
    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "evil.py").write_text("class Evil:\n    pass\n")
    (ws / "evil_link.py").symlink_to(outside / "evil.py")

    nodes, _edges, _rules = extract(ws)
    module_names = {n.get("name") for n in nodes if n.get("kind") == "module"}
    assert "evil" not in module_names, module_names


def test_repo_of_caps_to_workspace_root(tmp_path):
    """_repo_of resolves+commonpaths to the root; escapes return None."""
    from bo_extract.deterministic import _repo_of

    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    assert _repo_of(root, root / "a" / "b.py") == "a"
    assert _repo_of(root, outside / "x.py") is None
