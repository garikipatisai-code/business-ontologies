"""Manifest loading and verification for business-ontologies."""

import json
import os


def load_manifest(path):
    """Load a manifest JSON file and return it as a dict."""
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _as_manifest(m):
    if isinstance(m, (str, os.PathLike)):
        return load_manifest(m)
    return m


def verify_manifest(m, workspace_root=None, manifest_path=None):
    """Verify manifest paths are confined to the workspace root.

    Returns ``(ok, errors)``. Rejects absolute paths and symlink escapes.

    The workspace root defaults to the directory of the manifest file (when
    *manifest_path* is given) so an attacker cannot widen confinement through
    a manifest-supplied ``workspace_root``; an explicit *workspace_root*
    argument always wins.
    """
    m = _as_manifest(m)
    errors = []
    if not isinstance(m, dict):
        return False, ["manifest must be a JSON object"]

    if workspace_root is None:
        if manifest_path is not None:
            try:
                workspace_root = os.path.dirname(os.path.abspath(str(manifest_path)))
            except (OSError, ValueError):
                workspace_root = m.get("workspace_root", ".")
        else:
            workspace_root = m.get("workspace_root", ".")
    try:
        ws_real = os.path.realpath(os.path.abspath(workspace_root))
    except (OSError, ValueError) as exc:
        return False, [str(exc)]

    repos = m.get("repos")
    if not isinstance(repos, list):
        errors.append("repos must be a list")
        repos = []

    for i, repo in enumerate(repos):
        if not isinstance(repo, dict):
            errors.append(f"repo[{i}] must be an object")
            continue
        p = repo.get("path")
        if not isinstance(p, str) or not p.strip():
            errors.append(f"repo[{i}] missing path")
            continue
        if os.path.isabs(p):
            errors.append(f"repo[{i}] absolute path not allowed: {p}")
            continue
        parts = p.replace("\\", "/").split("/")
        if ".." in parts:
            errors.append(f"repo[{i}] path traversal not allowed: {p}")
            continue
        candidate = os.path.realpath(os.path.join(ws_real, p))
        try:
            if os.path.commonpath([ws_real, candidate]) != ws_real:
                errors.append(f"repo[{i}] path escapes workspace root: {p}")
                continue
        except ValueError:
            errors.append(f"repo[{i}] path escapes workspace root: {p}")
            continue

    return (len(errors) == 0, errors)