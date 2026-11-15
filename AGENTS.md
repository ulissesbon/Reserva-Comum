# PSI — Reserva Comum

This repository contains the shared-space reservation application developed for
PSI / IFCE. Keep work focused on the user's current request and preserve existing
application data and unrelated edits.

## Language

- Communicate with the project owner in Brazilian Portuguese.
- Write branch names, commit messages, pull-request titles and descriptions,
  README content, and new technical documentation in English.
- Preserve the current Brazilian Portuguese UI and persisted role/status values
  unless the user explicitly requests a language or data-contract change.

## Small, incremental commits

- Create a small, focused commit after each completed and appropriately verified
  feature, fix, or independent adjustment.
- Use Conventional Commits with an imperative English subject, for example:
  `feat: allow managers to block time slots` or
  `fix: prevent overlapping reservations`.
- Use branch names such as `feat/schedule-blocks`, `fix/booking-conflicts`,
  `chore/initial-import`, `ci/frontend-build`, or `docs/setup-guide`.
- Keep a change's implementation, relevant tests, and regenerated assets together.
  Each commit should leave the application coherent and runnable.
- Review the diff and stage explicit, task-related paths. Preserve unrelated user
  edits and do not bundle independent changes into a single commit.
- Push or merge only when requested as part of the user's task. Preserve the remote
  history; do not force-push, rewrite published commits, or discard local work.
- If Git writes or network access are blocked, report the exact limitation and
  provide the necessary local commands. Do not bypass filesystem restrictions.

## Implementation and verification

- Read only relevant files, nearby patterns, and tests. Prefer targeted `rg` and
  Git commands over full repository scans.
- Keep the existing React / Python standard-library / SQLite stack unless a
  requested change justifies a dependency or architecture change.
- For backend changes, run relevant tests and the suite when appropriate:
  `python3 -m unittest discover -v`.
- When TCP ports are unavailable, use
  `RESERVA_TEST_TRANSPORT=memory python3 -m unittest discover -v` and explicitly
  distinguish that result from TCP and browser verification.
- For frontend changes, rebuild with `npm ci` and `npm run build` in `frontend/`
  when dependencies need installation; otherwise use `npm run build`.
  Commit changed `frontend/src/` and `frontend/dist/` files together.
- State which checks ran, their outcome, and material verification limitations.
  Documentation-only changes normally require diff/content checks, not the full
  application test suite.

## Data and authorization

- Never commit runtime databases, passwords, session tokens, `.env` files, virtual
  environments, or installed `node_modules/` directories. Keep `.gitignore` current.
- Use temporary databases for tests. Preserve local accounts and reservations.
- Evolve existing databases with additive, repeatable migrations.
- Enforce roles, ownership, CSRF protection, and business rules on the server.
- Preserve transaction-based conflict checks, atomic updates, and append-only
  reservation/block audit history when modifying booking or blocking operations.
