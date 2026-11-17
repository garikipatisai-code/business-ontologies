# Debt Ledger

## Residual security LOWs (M1)

| ID | Debt | Impact | Owner |
|----|------|--------|-------|
| L1 | Hex digests excluded from redaction | 64-char hex secrets may pass unredacted | M2 |
| L2 | Entropy fallback and space/sentence tokens | Entropy heuristic edge cases on spaced text | M2 |
| L3 | Non-str excerpt values | Non-string provenance values could raise in excerpt path | M2 |
| L4 | `extract_file` direct symlink call | Direct call bypasses directory-walk symlink guard | M2 |
| L5 | `?repo=` absent returns all; bind host overridable | Over-broad read scope if exposed beyond localhost | M2 |

## Code questions (M1)

| ID | Question | Owner |
|----|----------|-------|
| Q1 | Entropy threshold/space handling in `_redact` | M2 |
| Q2 | `_redact` fallback fail-closed behavior | M2 |
| Q3 | `/rules` unfiltered (no repo/context scope) | M2 |
| Q4 | `OSError` skip semantics during extraction | M2 |
| Q5 | `origin`/`status` `None` handling | M2 |

## Deferred (post-M1)

| ID | Deferred | Reason | Owner |
|----|----------|--------|-------|
| D1 | Live CGR daemon | M1 uses import-guarded adapter/stub when live runtime unavailable | M2 |
| D2 | Java frontends | M1 structural extractor is Python `ast` only | M2 |
| D3 | Cypher full | CGR/Cypher subset limits remain | M2 |
| D4 | KAG reasoning | KAG is reference-only, not an M1 runtime dependency | M2 |
| D5 | LLM extraction live | M1 is deterministic/free-only | M2 |
| D6 | Multi-user auth | Read-only local inspect API only | M2 |
| D7 | YAML/SQL extractors | Text/config fixtures only in M1 | M2 |
