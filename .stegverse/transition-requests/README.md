# Transition request intake

A JSON file committed in this directory is a manifest. A push to `main` that adds or changes one runs `.github/workflows/organization-ledger-transition.yml` (job `request-transition`). That job appends the request to `refs/stegverse/organization-ledger` in `SV-011/.github`, publishes the ref fast-forward only, and reads it back from a fresh fetch.

Storage is the layout already on that ref. The repository receipt goes to `repository-ledger/SV-011/.github/`. The organization receipt goes to `receipts/` and names the repository receipt in `repo_receipt_sha256`. Receipts already on the ref are not migrated or rewritten.

The request schema is `stegverse.organization-transition-request/v1`. Required fields:

- `request_id`, `owning_existing_goal`
- `standing` with `mode` and `node_ref`
- `transition_id`, `transition_class`, `org_transition_class`
- `predecessor_state_sha256`, `successor_state_sha256` (`sha256:` plus 64 hex digits)
- `authority_constraint` (the authority constraint variable, recorded as given)
- `evidence` (an object; it can be empty)

Optional `hb_epoch` is an integer time constraint.

Optional `current_state` follows the Current-state basis section of `Admissible-Existence/TT` commit `ba3cca71b6928cf739ab6b321c939860fd1edbb1`, `docs/SV_011_NEXT_REQUIRED_CAPABILITY_DERIVATION_CONTRACT.md` (blob `8f7d4cc2127325b155e1a91edfe531b5737f7c75`). It declares `state_id`, `as_of`, `fresh_until`, `declared_transition_element`, `declared_role_escalation`, and `satisfied_scope_ids`:

- `as_of` and `fresh_until` are `YYYY-MM-DDTHH:MM:SSZ`, with `as_of` not later than `fresh_until`.
- `declared_transition_element` has `transition_id` (`T-` plus three digits) and `fixture_ref` (`fixtures/transition-elements/<name>.json`, `<name>` in `[a-z0-9.-]+`). The fixture is a file in the TT tree. This repository does not open it; the TT engine does, against its own root.
- `declared_role_escalation` has `role_id`, `from_role`, and `to_role`. The two roles differ. Identifiers match `^[A-Za-z0-9._:-]{1,128}$`.
- `satisfied_scope_ids` is `[]`, `["transition_element"]`, or `["transition_element", "role_escalation"]`.

A `current_state` that the readback would not accept as a basis is refused before anything is appended. The six values are copied onto the organization receipt. This intake does not fill in a missing one. The readback computes `basis_digest` as the lowercase hex SHA-256 of `current_state` with that field removed (`json.dumps`, `sort_keys=True`, `separators=(",", ":")`, `ensure_ascii=False`).

`build_manifest` and `manifest` are refused with `SDK_MANIFEST_BUILDER_NOT_PRESENT`. This repository does not build or admit an SDK manifest.

No request is committed here. Committing one is the manifest that appends.
