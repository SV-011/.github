# Declarations

DEFAULT pending owner confirmation (B2). A file here is a transition request that has not been submitted. It sits outside `.stegverse/transition-requests/`, so a push to `main` does not append it and nothing on `refs/stegverse/organization-ledger` refers to it.

`SV011-ORG-STATE-0001.request.json` declares the first `current_state` for SV-011/.github:

- `state_id` `SV011-ORG-STATE-0001`, `as_of` 2026-10-17T14:04:30Z, `fresh_until` seven days later.
- `declared_transition_element` T-181, `fixtures/transition-elements/t-181.receipt-record.json`. `Admissible-Existence/TT@ba3cca71` has no t-011 fixture, so this is the named fallback.
- `declared_role_escalation` `sv011.context-to-evidence`, `context` to `evidence`; `satisfied_scope_ids` `[]`.
- `predecessor_state_sha256` is the live head organization receipt (`sha256:3db9008d…`); `successor_state_sha256` is the `basis_digest` of the declared `current_state`.

Submitting it is a separate commit that moves the file, unchanged, into `.stegverse/transition-requests/` on `main`. That commit is the manifest that appends. If it lands after `fresh_until`, the readback reports the basis as stale; restate `as_of` and `fresh_until` and recompute `successor_state_sha256` first.

The expected result of that append is declared in `.stegverse/build-expectations/sv011-first-cycle.json` (B9).

Revert: delete the file. It has appended nothing.
