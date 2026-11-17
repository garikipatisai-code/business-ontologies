---
spec: docs/upstream-assessment.md
status: approved
approved_by: human-2026-11-17
files:
  allow:
    - docs/plans/auto-ontology-assessment.md
    - docs/plans/auto-ontology-assessment-progress.md
Goal: Auto-ontology assessment via code-graph-rag dependency (pip a4132ce7). Scope: docs-only; free public upstream; no implementation code; human-2026-11-17 approved.

mermaid: flowchart TD
    A[Start] --> B[Explore UA/CGR/KAG candidates]
    B --> C[Verify code-graph-rag integration feasibility]
    C --> D[Build business-ontology domain layer]
    D --> E[Validate provenance & testing]
    E --> F[Persist assessment ledger]

Architecture:
- Business-ontology domain layer (in-house, using UA domain→flow→step prompt pattern)
- code-graph-rag dependency (pip, pinned a4132ce7)
- No second parser, queue, or database beyond CGR

Tech Stack:
- code-graph-rag: pip install code-graph-rag==0.7.1
- Python>=3.12, treesitter-full optional
- domain layer: internal codebase (UA pattern adapted)

Spec: docs/upstream-assessment.md (status approved approved_by human-2026-11-17)
Global+Lean Constraints:
- No assessment content yet (T1 gate not yet opened)
- Docs-only, public upstream, free-only
- Human approved bounded read-only assessment
- M2 commit 3e0c80f preserved; no product changes

Review Focus:
- Integration feasibility of CGR for auto-ontology
- Provenance matching with UA patterns
- Testing strategy for domain layer

Task table:
| T1 | T2 | T3 |
|----|-----|-----|
| Build domain layer (ad-hoc prompts) | Initialize CGR graph with provenance tracking | Persist assessment ledger + progress |

Per-task notes:
- T1: Start with UA domain→flow→step prompt pattern; build minimal domain layer; no ontology content yet.
- T2: Integrate CGR dependency; configure shared graph with manifest; verify verify-index/diff-index.
- T3: Write progress ledger; verify git status; complete bounded assessment.

Requirement coverage:
- Docs-only: ✓
- Free public upstream: ✓
- No implementation code: ✓
- Human approved: ✓
- M2 commit preserved: ✓

Test integrity:
- No tests required (docs-only assessment)
- Ledger persistence verification step
---