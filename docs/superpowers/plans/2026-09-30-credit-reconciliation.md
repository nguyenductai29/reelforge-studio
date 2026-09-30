# Phase 3.7 Credit Reconciliation Implementation Plan

> **For agentic workers:** Use test-driven development for accounting and worker behavior, then independent review before completion.

**Goal:** Resolve uncertain paid steps through explicit, audited, idempotent admin decisions.

**Architecture:** Keep the existing credit ledger and usage events. Add one immutable reconciliation decision per step, infer pending from an unreconciled paid `needs_attention` step, and reuse existing reservation/refund references. Store sanitized provider progress in step output. Lock the run and account when resolving, and require terminal jobs before accounting changes.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, unittest, Next.js, React Query.

**Spec:** User-provided Phase 3.7 specification in this conversation; operator behavior documented in `docs/CREDIT_RECONCILIATION.md`.

## Global Constraints

- Work on the requested `feat/studio-foundation` checkout; preserve earlier phases and existing UI layout.
- No new AI capabilities, pricing, subscriptions, payment methods, or publishing platforms.
- Never mutate previous ledger entries; use frozen reservation amounts and unique references.
- Use mocked providers only. Do not read secrets or migrate the live database.

## Review Focus

- Concurrent opposite decisions must produce one immutable outcome.
- Legacy attention steps without new metadata must remain resolvable.
- Mismatched workspace/run/step/reservation relationships must reject accounting changes.
- Existing usage/refunds must never be duplicated or reversed by a conflicting action.
- Full storage and uncertain provider failures must not trigger another paid submit.

## Tasks

1. Accounting and API (`app/reconciliation.py`, `app/models.py`, migration 0011, `app/main.py`, `tests/test_reconciliation.py`). Write failing integration tests, add immutable decisions and transactional resolution, safe admin projections, history and retry guard. Verify accounting, authorization, races and migration preservation.
2. Worker safety (`app/video_worker.py`, `app/text_worker.py`, worker tests). Test before changing storage preflight, sanitized progress metadata and uncertainty handling. Keep deterministic text refunds and successful execution intact; document conservative provider decisions.
3. Admin UI (`frontend/src/components/reelforge/reconciliation.tsx`, Admin page, types and translations). Add pending/history views, details and confirmed actions with optional note using the existing interface components. Clarify regular-user attention messages.
4. Documentation and verification. Update implementation status and operator guide; run the complete backend suite, frontend typecheck/build, and independent review. Report migration, routes, behavior and limitations.

## Progress

- Initial handoff review complete; branch clean at `769a446`.
- Implementation authorized by the supplied detailed specification. Work split by file ownership between backend accounting, workers, and frontend.
- Tasks 1–4 complete. Backend: 274 tests, 5 skipped; frontend typecheck/build passed; diff check passed.
- Review fixes: classify post-200 empty/filtered text as uncertain; safely project malformed legacy metadata; acquire run before account in workers to match reconciliation. The fresh reviewer verified all three corrections.
- Migration 0011 tested on a disposable 0010 database with old-table snapshots unchanged, including balances, usage, jobs, assets and publications. No live migration or paid request was executed.
- Changes remain in the requested working branch for review; no commit, push or deployment was requested.
