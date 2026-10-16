# Transition request intake

A JSON file committed in this directory is a manifest. Pushing it to `main` runs `.github/workflows/organization-ledger-transition.yml`, which appends that request to `refs/stegverse/organization-ledger` in `SV-011/.github` and reads the ref back.

The request schema is `stegverse.organization-transition-request/v1`. Required fields:

- `request_id`, `owning_existing_goal`
- `standing` with `mode` and `node_ref`
- `transition_id`, `transition_class`, `org_transition_class`
- `predecessor_state_sha256`, `successor_state_sha256` (`sha256:` plus 64 hex digits)
- `authority_constraint`
- `evidence` (object; empty is allowed)

Optional `hb_epoch` is an integer time constraint. Optional `current_state` may declare `state_id`, `as_of`, `fresh_until`, `declared_transition_element`, `declared_role_escalation`, and `satisfied_scope_ids`. `as_of` and `fresh_until` must be `YYYY-MM-DDTHH:MM:SSZ`. Those six values are copied onto the organization receipt. This intake does not fill in a missing one, including either `declared_*` field. Readback computes `basis_digest` as the lowercase hex sha256 of those six fields.

`build_manifest` and `manifest` are refused. This repository does not build or admit an SDK manifest.

No request is committed here. Committing one is the manifest that appends.
