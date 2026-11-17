# Third-Party Notices — Milestone 0

Date: 2026-11-17 | Account: console-openrouter

The business-ontologies platform incorporates or references the following third-party software. This file documents attribution only; no implementation code or upstream checkout is included in this milestone.

## Dependencies (pinned)

### code-graph-rag (CGR)

- Project: https://github.com/vitali87/code-graph-rag
- Pin: commit a4132ce7de72222f8de88d41a41f2ab80c87dfe8e (v0.7.1), 2026-11-17
- License: MIT
- Copyright: ©2025 Vitali Avagyan
- Use: structural foundation (dependency, pip pinned). See ADR 0001 / ADR 0002.

MIT License text:

> MIT License
>
> Copyright (c) 2025 Vitali Avagyan
>
> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this software and associated documentation files (the "Software"), to deal
> in the Software without restriction, including without limitation the rights
> to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
> copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all
> copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
> IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
> FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
> AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
> LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
> SOFTWARE.

## Pattern attribution (no fork, no dependency)

### Understand-Anything (UA)

- Project: https://github.com/Egonex-AI/Understand-Anything
- Pin: commit b861852dda79273bd9d2e01c3160d42e2df510d2 (v2.9.8), 2026-11-15
- License: MIT
- Copyright: ©2026 Yuxiang Lin/Infinite Universe
- Use: domain→flow→step prompt pattern adapted for the in-house business-ontology layer only (attribution; no fork, no code copied).

## Reference only (not incorporated)

### KAG (OpenSPG)

- Project: https://github.com/OpenSPG/KAG
- Pin: commit fdab15b3929d2ee40dfcdd388f90233096a6afc9 (v0.8.0), 2026-01-28
- License: Apache-2.0
- Copyright: OpenSPG
- Use: reference only; no integration, no dependency, no fork.
