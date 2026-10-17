# Transition request intake

A JSON file committed in this directory is a manifest. A push to `main` that adds or changes one runs `.github/workflows/organization-ledger-transition.yml` (job `request-transition`). That job appends the request to `refs/stegverse/organization-ledger` in `SV-011/.github`, publishes the ref fast-forward only, and reads it back from a fresh fetch.

Storage is the layout already on that ref. The repository receipt goes to `repository-ledger/SV-011/.github/`. The organization receipt goes to `receipts/` and names the repository receipt in `repo_receipt_sha256`. Receipts already on the ref are not migrated or rewritten.

The request schema is `stegverse.organization-transition-request/v1`. Required fields:

- `request_id`, `owning_existing_goal`
- `constraint_set` with `node_standing` (`mode` `ESTABLISH_GENESIS` or `VERIFY_EXISTING`, `node_ref`, and the `predecessor` key, `null` for genesis) and an optional `declared` block (`authority`, `time`: `satisfies` or `does_not_satisfy`, stored as declared only). `VERIFY_EXISTING` is refused with `PREDECESSOR_BINDING_PRIMITIVE_NOT_PRESENT`. DEFAULT pending owner confirmation (B1).
- `manifested_route` equal to `transition_request_intake.manifested_route_binding` in `.stegverse/transition-ledger/org-contract.json`, else `ROUTE_NOT_BOUND_IN_THIS_ORGANIZATION`. DEFAULT pending owner confirmation (B5).
- `transition_id`, `transition_class`, `org_transition_class`
- `predecessor_state_sha256`, `successor_state_sha256` (`sha256:` plus 64 hex digits)
- `authority_constraint` (the authority constraint variable, recorded as given)
- `evidence` (an object; it can be empty)

Optional `hb_epoch` is an integer time constraint. Optional `purpose` is free text. A top-level `standing`, or any other field, is refused.

Optional `current_state` follows the Current-state basis section of `Admissible-Existence/TT` commit `ba3cca71b6928cf739ab6b321c939860fd1edbb1`, `docs/SV_011_NEXT_REQUIRED_CAPABILITY_DERIVATION_CONTRACT.md` (blob `8f7d4cc2127325b155e1a91edfe531b5737f7c75`). It declares `state_id`, `as_of`, `fresh_until`, `declared_transition_element`, `declared_role_escalation`, and `satisfied_scope_ids`:

- `as_of` and `fresh_until` are `YYYY-MM-DDTHH:MM:SSZ`, with `as_of` not later than `fresh_until`.
- `declared_transition_element` has `transition_id` (`T-` plus three digits) and `fixture_ref` (`fixtures/transition-elements/<name>.json`, `<name>` in `[a-z0-9.-]+`). The fixture is a file in the TT tree. This repository does not open it; the TT engine does, against its own root.
- `declared_role_escalation` has `role_id`, `from_role`, and `to_role`. The two roles differ. Identifiers match `^[A-Za-z0-9._:-]{1,128}$`.
- `satisfied_scope_ids` is `[]`, `["transition_element"]`, or `["transition_element", "role_escalation"]`.

A `current_state` that the readback would not accept as a basis is refused before anything is appended. The six values are copied onto the organization receipt. This intake does not fill in a missing one. The readback computes `basis_digest` as the lowercase hex SHA-256 of `current_state` with that field removed (`json.dumps`, `sort_keys=True`, `separators=(",", ":")`, `ensure_ascii=False`).

`build_manifest` and `manifest` are refused with `SDK_MANIFEST_BUILDER_NOT_PRESENT`. This repository does not build or admit an SDK manifest.

No request is committed here. Committing one is the manifest that appends. A push that adds or changes no request file appends nothing.

## Dispositions

An applied request writes `disposition: ALLOW` into the evidence of both receipts and into the result, as evidence that the append occurred. A request that carries its own `evidence.disposition` is refused.

A refused request is `FAIL_CLOSED` with `failure_code: ORGANIZATION_TRANSITION_REQUEST_REFUSED` and the six fields of the Organization Role conformance standard: `failure_code`, `failed_predicate`, `required_evidence_or_repair`, `retry_entrypoint`, `owning_existing_goal` (the request's own, else `SV011-TT-CAPABILITY-MAP-OBSERVATION-001`) and `next_attempt`. It is refused before the append and leaves no receipt (`refusal_recorded: false`). See `docs/ORGANIZATION_ROLE_CONFORMANCE.md`.

## Readback

`resident-runtime/organization_ledger_readback.py` observes the ref and records no transition. A successful read has no top-level `disposition` and exits 0. A refusal, or a read holding the declared legacy segment, carries a top-level `disposition: FAIL_CLOSED` with the six fields and exits 1. An empty ledger (no HEAD) is refused `ORGANIZATION_LEDGER_HEAD_ABSENT` on `ORGANIZATION_LEDGER_HEAD_PRESENT` (C6 parity 3). These are DEFAULTs pending owner confirmation. Whether a successful read becomes `ALLOW` under readback/v1 (C6 parity 4) is pending Rigel's decision on #12 item 1. A copy of the ledger is read through a `git+` locator (a clone, or `tests/fixtures/organization-ledger-2cc58cec.bundle`), not a directory copy. A ledger that does not hold the declared segment is read with `--org-contract` naming a contract that declares none. The report covers two things separately:

- `verification`: whether the organization chain verifies, whether the repository chain verifies, and whether every organization receipt names a repository receipt that is present. A closure result handed to it must match the `evidence.disposition` already inside the receipts it names (`closure_result_matches_receipt_evidence`).
- the current-state basis: `basis_established`, `current_state_reason`, and `current_state`. A basis that is not established also carries the six fields in `current_state_finding`; a refused readback carries them at the top level.

`source_receipt_retention` reports, per organization receipt, whether its source receipt is retained under `source-receipts/` (DEFAULT pending owner confirmation, H45). The receipt at tip `2cc58cec` predates retention and is the declared legacy segment, so a read of the live ref is `FAIL_CLOSED` `DECLARED_LEGACY_SEGMENT_NOT_SOURCE_RETAINED` with the six fields (never ALLOW); it is not rewritten.

A disposition appears only inside the original receipts, as evidence that a transition occurred. It does not satisfy the authority constraint or the time constraint.

The head receipt on the live ref (`2cc58cec`, organization receipt `sha256:3db9008d…`) carries no `current_state`, so the readback reports `BASIS_ABSENT` with `current_state` null. That code is never turned into a current state.

`Admissible-Existence/TT@ba3cca71` has no `fixtures/transition-elements/t-011.json`. A request that names a fixture TT does not hold is accepted here, because this repository does not open the fixture, and TT reports `BASIS_UNKNOWN` for it (`fixture_ref_resolution`).

## Open

`standing` is replaced by `constraint_set` as a DEFAULT pending owner confirmation (SV-011/.github#12, item 2; option B). The readback schema id is `stegverse.organization-ledger-observation/v1` (B3). The first `current_state` is held, unsubmitted, in `.stegverse/declarations/` (B2). See `docs/ORGANIZATION_ROLE_CONFORMANCE.md`.
