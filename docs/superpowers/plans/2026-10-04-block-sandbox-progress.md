# SDD ledger — plan: docs/superpowers/plans/2026-10-04-block-sandbox.md

User approved spec and authorized autonomous work without intermediate questions.
Execution: native inline with one independent final review.
Pre-flight: Tasks 1→2 world block API, 1→3 edits, 1–3→4 simulation/snapshot: interfaces consistent.
Pre-flight: Task 4→5 HTTP startup smoke uses the final server; temporary failing smoke is expected until Task 5.
Ruling: Work in an isolated new project directory in this checkout, preserving all existing dirty stock files; no copied old stock checkout or environment. Cost if wrong: game relocation is simple because it is self-contained.
Ruling: User explicitly waived subsequent approval gates; self-review plan and proceed. Cost if wrong: scope correction may require rework; approved spec remains authoritative.
Task 1: complete — seed/tree determinism, negative edits, bounds and mesh tests RED→GREEN (8/8).
Task 2: complete — floor/wall/ceiling, flight/diagonal, safe spawn and DDA tests RED→GREEN (9/9).
Task 3: complete — schema/air edits/defensive copies/ordered writes/failure recovery RED→GREEN (8/8). Combined tested core commit 666779b; suite 25/25.
Task 5 partial: server/HTTP tests written RED first and implemented with scene scaffolding; suite now 30/30.
Ruling: Task 4 actual HTTP startup needs Task 5 server, so implement server tests/launcher alongside UI instead of delaying runtime checks. Cost if wrong: task grouping only, no scope change.
Existing stock baseline: venv Python unittest discover — 49 tests, OK, 43.538 seconds; tmp/block-sandbox-stock-baseline.log.
Task 4: complete — real Edge input/save/import/offline flows, self-contained renderer, Chinese UI and local pixel atlas; browser smoke passed.
Final review: independent read-only reviewer; two Important findings, no Critical or Minor.
Final: fixed import visibility-save race — deferred real queue/browser state RED→GREEN; ordinary saves blocked and replacements serialized during transition.
Final: fixed wet initial spawn seed 23 — physics test RED→GREEN; legitimate saved underwater position stays unchanged.
Final: fixed home import-error visibility — actual visible status assertion RED→GREEN.
Final: fixed new-world setting reset and explicit Escape fallback — actual browser assertions RED→GREEN.
Final: Ruling: survival/multiplayer/fluid/Minecraft save compatibility remain excluded per approved first-release scope; cost if wrong: additional subsystem work.
Final: Ruling: desktop Chromium is the supported target; mobile/other browsers need separate input/render validation; cost if wrong: unsupported-device incompatibility.
Final: Ruling: reviewer did not rerun performance/package validation, so executor verifies real browser and ZIP contents; cost if wrong: evidence only covers this machine.
Final: Ruling: pinned Three.js internals are treated as third-party code with license; no source alteration; cost if wrong: dependency defects need later library updates.
Final test evidence 2026-10-05: npm test 34/34; JS syntax checks clean; real Edge smoke and edge suites PASS, zero page errors and external requests. Low-view distance sampled after completed loading: 25 meshes, 45–48 cached chunks; medium steady state about 60 FPS on RTX 4060 Laptop GPU.
Task 5: complete — README/LEARNING/VALIDATION, local launcher, port identity, final reviewed fixes, evidence and allowlisted release builder. Keep local artifacts in place; user explicitly waived intermediate integration questions. No remote publication.
