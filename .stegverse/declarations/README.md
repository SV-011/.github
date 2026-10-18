# Declarations

DEFAULT in force (fail-closed) (B2). This directory holds transition requests that are declared but not staged. It is empty now.

`SV011-ORG-STATE-0001.request.json` was declared here and is now staged, unchanged, in `.stegverse/transition-requests/` (#167 comment 6245553028, item 3: class A, the manifest-directed append through `.github/workflows/organization-ledger-transition.yml` job `request-transition`). When PR #9 is admitted, the push to `main` that adds it is the manifest that appends it. Nothing is dispatched.

It declares the first `current_state` for SV-011/.github:

- `state_id` `SV011-ORG-STATE-0001`, `as_of` 2026-10-17T14:04:30Z, `fresh_until` 2026-10-24T14:04:30Z.
- `declared_transition_element` T-181, `fixtures/transition-elements/t-181.receipt-record.json`. `Admissible-Existence/TT@ba3cca71` has no t-011 fixture, so this is the named fallback.
- `declared_role_escalation` `sv011.context-to-evidence`, `context` to `evidence`; `satisfied_scope_ids` `[]`.
- `predecessor_state_sha256` is the live head organization receipt (`sha256:3db9008d…`); `successor_state_sha256` is the `basis_digest` of the declared `current_state`.

The intake checks `as_of <= fresh_until` and reads no clock. If admission lands after `fresh_until`, restate `as_of` and `fresh_until` and recompute `successor_state_sha256` in the staged file first (B2 fail-closed default).

The expected result of the append is declared in `.stegverse/build-expectations/sv011-first-cycle.json` (B9).

Revert: move the file back here. It has appended nothing.
