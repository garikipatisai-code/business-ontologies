"""CGR adapter interface.

Live CGR shape (Java/Spring-first) targeted by this adapter:

- ``index(repo, workspace)``: build/refresh a ``.cgr`` unified-workspace entry
  for one project; returns the workspace path plus an index summary.
- ``verify_index(workspace)``: integrity check over the embedded SQLite graph
  (tables present, project count, no broken edges).
- ``diff_index(workspace)``: incremental diff since the last indexed revision;
  returns added/changed/removed file fingerprints.
- ``resolve_definition(symbol, workspace)``: locate the definition node for a
  Java class/method (or TS function) with ``file`` + ``line``.
- ``resolve_callers(symbol, workspace)``: traverse CALLS edges backwards to the
  calling methods, each with ``file`` + ``line``.

When the optional ``cgr`` dependency is absent, every method returns a
deterministic stub record (origin ``deterministic``) so the rest of the
platform stays hermetic and testable.
"""

from __future__ import annotations

ORIGIN = "deterministic"


try:  # optional dependency: code-graph-rag
    import cgr  # type: ignore
except Exception:  # pragma: no cover - depends on environment
    cgr = None


class CgrAdapter:
    """Import-guarded adapter over code-graph-rag's unified workspace."""

    def __init__(self, workspace=None):
        self.workspace = workspace
        self.available = cgr is not None

    def _stub(self, operation, **fields):
        record = {
            "operation": operation,
            "origin": ORIGIN,
            "workspace": self.workspace,
            "records": [],
        }
        record.update(fields)
        return record

    def index(self, repo, workspace=None):
        workspace = workspace or self.workspace
        if cgr is None:
            return self._stub("index", repo=str(repo), workspace=workspace)
        return cgr.index(str(repo), workspace=workspace)

    def verify_index(self, workspace=None):
        workspace = workspace or self.workspace
        if cgr is None:
            return self._stub("verify_index", workspace=workspace, ok=True)
        return cgr.verify_index(workspace=workspace)

    def diff_index(self, workspace=None):
        workspace = workspace or self.workspace
        if cgr is None:
            return self._stub(
                "diff_index", workspace=workspace, added=[], changed=[], removed=[]
            )
        return cgr.diff_index(workspace=workspace)

    def resolve_definition(self, symbol, workspace=None):
        workspace = workspace or self.workspace
        if cgr is None:
            return self._stub(
                "resolve_definition", symbol=symbol, workspace=workspace
            )
        return cgr.resolve_definition(symbol, workspace=workspace)

    def resolve_callers(self, symbol, workspace=None):
        workspace = workspace or self.workspace
        if cgr is None:
            return self._stub(
                "resolve_callers", symbol=symbol, workspace=workspace
            )
        return cgr.resolve_callers(symbol, workspace=workspace)
