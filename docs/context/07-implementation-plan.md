# Implementation plan — reviewable steps

A build order for E.C.H.O expressed as small pull requests. Each step is one reviewable idea: you
read it top to bottom, decide whether it is right, and move on. The order is a dependency order —
step N assumes every earlier step merged.

Scope of the detailed part: **the Training vertical slice, end to end** (p.11, `CLAUDE.md` → Build
order). Submit a training job through the API, plan it, materialize it in Kubernetes under Kueue,
observe it to a terminal result, cancel it, expire it. Everything after that is listed coarsely in
[Phase 7](#phase-7--after-the-slice) because several of those steps depend on open questions
(`99-open-questions.md`).

## Rules for every step

1. **One idea per PR.** If the review comment "this is two changes" is available, the PR is too big.
2. **Ceiling of ~400 changed lines**, tests included. The sizes below are estimates; if one is
   badly wrong when we get there, split it rather than exceed it.
3. **Every PR ships its own tests.** No "tests in a follow-up".
4. **A merged migration is immutable.** Fixes go in a new migration, never as an edit — the point
   of hand-written migrations (D5) is that the file you reviewed is the file that ran.
5. **Every PR names the invariants it touches**, using the numbering in `CLAUDE.md` →
   Non-negotiable invariants. A PR that touches an invariant and does not name it is the failure
   mode this list exists to prevent.
6. **SQL and YAML lifted from the specification are reproduced verbatim** (`docs/context` →
   Conventions). A reviewer should be able to diff them against `03-data-and-worker-protocol.md`
   character for character.
7. **No Kubernetes call inside an open PostgreSQL transaction** (invariant #3). This is a review
   question on every worker PR, not only the ones that mention it.

## Assumed repository layout

Introduced by step 01. Flagged here because it is an assumption, not a decision recorded in
`06-decisions.md` — see [Questions](#questions-before-step-01) Q1.

```text
control-plane/          # Python 3.12, FastAPI. D1, D4
  echo/
    api/                # entrypoint: HTTP
    planner/            # entrypoint: placement validation
    scanner/            # entrypoint: expiry, recovery, directory sync
    policy/             # policy resolution, no I/O
    db/                 # SQLAlchemy models, session, claim statements
    directory/          # LDAP client
  migrations/           # Alembic, hand-written
  tests/
worker/                 # Go, controller-runtime. D1, D3
  api/v1alpha1/         # EchoOperation, EchoExecution types
  cmd/compute-worker/   # D12 identity: training + research. Step 01
  cmd/services-worker/  # D12 identity: inference. Step 01, empty until Phase 7
  cmd/project-worker/   # D14 identity. Arrives with step 40, not before
  internal/
web/                    # React + TypeScript + Vite. D2
deploy/                 # CRDs, RBAC, kustomize overlays
```

---

## Phase 0 — Foundations

Nothing in this phase encodes a domain rule. The goal is that phase 1 can be reviewed as pure
schema, with a test harness already trusted.

### 01 — Monorepo skeleton and toolchain

**Goal.** Four buildable, empty projects and one CI pipeline.

**Files.** `control-plane/pyproject.toml` (ruff, mypy, pytest), three console entrypoints that
print their name and exit; `worker/go.mod` + `main.go` per identity; `web/` Vite scaffold;
`Makefile`; `.github/workflows/ci.yml`; `deploy/.gitkeep`.

**Review for.**
- The three Python entrypoints are separate processes over one package (D4), not one process with a
  mode flag.
- Two worker binaries exist from the start — `compute-worker` and `services-worker` — because a
  Pod carries one ServiceAccount, so D12's two workload identities cannot be one binary with a
  mode flag (D12). `project-worker` is not created here; it arrives with step 40.
- CI runs lint, type-check and test for all three languages and fails on any.

**Done when.** `make lint test build` is green on a clean checkout.

**Size.** ~250 lines, nearly all configuration.

### 02 — Postgres dev stack, Alembic runner, baseline migration

**Goal.** A migrated empty database, reproducible locally and in CI.

**Files.** `docker-compose.yml` (Postgres 16 only); `control-plane/migrations/env.py` +
`0001_baseline.py`; `make db-up db-migrate`; CI step running `alembic upgrade head` then
`alembic check`.

**Review for.**
- `alembic check` runs in CI and fails on drift (D5). Confirm it fails: the PR should include the
  proof, e.g. a deliberately drifted model in a test, not a claim.
- `0001_baseline` creates the `echo` schema and a `schema_meta` table holding one row with the
  expected schema version — the value the Go worker refuses to boot without (D5).
- Migrations run only from the Python side. No Go migration code, now or later.

**Done when.** A fresh database migrates to head in both environments; `alembic check` is clean.

**Size.** ~150 lines.

### 03 — Test harness: an ephemeral migrated database

**Goal.** Both languages test against a real migrated database, because that is what protects the
cross-language boundary (D5).

**Files.** `control-plane/tests/conftest.py` (session-scoped container or template database,
per-test transaction rollback); `worker/internal/testdb/testdb.go` (same database, migrated by the
Python runner in CI, connection helper for Go).

**Review for.**
- No SQLite, no mocked database anywhere. Every rule from phase 1 lives in PostgreSQL, so a fake
  tests nothing.
- Go tests do not migrate; they consume a database migrated by the Python runner.

**Done when.** A trivial `SELECT 1` test passes in both languages against the same migrated schema.

**Size.** ~200 lines.

---

## Phase 1 — The database is the protocol

This is the phase that decides whether the system is correct (D6). It is also the phase where a
reviewer has the most leverage, so the steps are deliberately small. No application logic lands
here — every PR is DDL plus tests written as SQL assertions.

### 04 — Lifecycle state catalog and allowed transitions

**Goal.** The p.7 lifecycle as data, before anything can write it.

**Files.** `0002_lifecycle_states.py` — `operation_state` enum (or a `state` table with a
`terminal boolean`), plus an `operation_state_transition` table holding the allowed edges;
`tests/db/test_lifecycle_catalog.py`.

**Review for.**
- `Checkpointing` is **absent** (D16). Its presence would be a bug even though the p.7 diagram
  draws it.
- `Unavailable` and `Relocating` are present and non-terminal (D26).
- The terminal set is exactly `Succeeded, Failed, Cancelled, Expired, Rejected` (invariant #7) —
  five, and `PlacementFailed` is *not* among them, it is a reason on `Failed` (D20).
- `Retrying` points back to `Queued`, never to `Starting` (D16).

**Done when.** A test enumerates the edge set and compares it to the state graph in
`02-domain-model.md`. That test is the specification's diagram, executable.

**Invariants.** #7.

**Size.** ~180 lines, mostly the table of edges and the test that mirrors it.

### 05 — `compute_operation` and `operation_revision`

**Goal.** Desired state and its revision history.

**Files.** `0003_operation_tables.py`; `tests/db/test_operation_tables.py`.

**Review for.**
- `UNIQUE (owner_subject, idempotency_key)` exists as a constraint (invariant #12, p.9).
- `owner_subject` is documented in the migration as an immutable directory identifier
  (`objectGUID` / `entryUUID`) and is **not** a DN or username (D7). A comment on the column, so
  the next person cannot get it wrong.
- `desired_generation` lives on the operation; a revision row carries the intent that produced it
  (`03-data-and-worker-protocol.md` → four counters).
- The policy snapshot is **one** snapshot per operation (D10), not JSONB per revision.
- Time-bound columns (`approved_duration`, `admitted_at`, `expires_at`) exist but nothing sets
  `admitted_at` yet — that is step 28, and it must not be settable at submission (invariant #9).

**Done when.** A duplicate `(owner_subject, idempotency_key)` insert raises; a revision insert
without an operation raises.

**Invariants.** #9, #12.

**Size.** ~250 lines.

### 06 — The transition function and the terminal-guard trigger

**Goal.** The single write path into the state machine, and the backstop under it (D6).

**Files.** `0004_transition_function.py` — `echo_operation_create(...)` and
`echo_operation_transition(...)` in PL/pgSQL, plus a `BEFORE UPDATE` trigger;
`tests/db/test_transitions.py`.

**Review for.**
- There is a **create** path as well as a transition path, because the submission transaction is
  itself an entry point into the machine (D6).
- The trigger rejects a terminal→anything update issued as an ad-hoc `UPDATE`, i.e. bypassing the
  function. The test must attempt exactly that. This is where a reviewer verifies invariant #7 —
  not in Python, not in Go.
- Every transition writes an `operation_event`… **except** that `operation_event` does not exist
  until step 09. Either this PR moves after 09, or it takes an explicit follow-up note. Reviewer's
  call; my preference is to merge 09 first and fold event-writing in here.
- The function validates against the step-04 edge table rather than repeating the graph.

**Done when.** Terminal states cannot move backward by any route the tests can find, including
`UPDATE compute_operation SET state = ...` as the table owner.

**Invariants.** #7.

**Size.** ~300 lines.

### 07 — `reconcile_queue`

**Goal.** The durable queue, with the column semantics the specification's SQL depends on.

**Files.** `0005_reconcile_queue.py`; `tests/db/test_queue_shape.py`.

**Review for.**
- Every column named in `03-data-and-worker-protocol.md` is present with those exact names:
  `operation_id, state, available_at, priority, lease_owner, lease_until, claim_epoch,
  desired_generation, processed_generation`.
- `state` allows exactly `pending`, `claimed`, `idle`.
- A partial index supports `WHERE state = 'pending' AND available_at <= now() ORDER BY priority
  DESC, available_at` — the claim is the hot path and the index must match its shape, including the
  `DESC`.
- The queue row is one per operation, not one per attempt.

**Done when.** `EXPLAIN` on the step-08 claim query uses the partial index; a test asserts that.

**Invariants.** #4.

**Size.** ~200 lines.

### 08 — Claiming and completion, verbatim, with fencing tests

**Goal.** The two statements the whole system's safety rests on, and the tests that prove they
behave as claimed.

**Files.** `0006_queue_functions.py` (the claim CTE and the fenced completion `UPDATE`, wrapped as
SQL functions so both languages call one definition); `tests/db/test_claiming.py`.

**Review for.**
- The claim body is character-identical to p.10 as transcribed in
  `03-data-and-worker-protocol.md`, including `FOR UPDATE SKIP LOCKED` and
  `claim_epoch = claim_epoch + 1`.
- The completion `UPDATE` carries `AND lease_owner = $worker_id AND claim_epoch = $claim_epoch`,
  and returns a row count the caller must check.
- The `CASE` re-arm — `desired_generation > processed_generation → 'pending'` — is reproduced, not
  reinterpreted.
- Tests, each of which must fail if the corresponding clause is deleted:
  - two concurrent claimers get different rows (`SKIP LOCKED`);
  - a stale claimer's completion updates **zero** rows and its result is discarded (invariant #6);
  - a re-claim after lease expiry bumps only `claim_epoch` — no new revision, no new attempt
    (four-counters table).

**Done when.** All four tests pass, and each has been shown to fail with its clause removed.

**Invariants.** #4, #6.

**Size.** ~300 lines, mostly concurrency tests.

### 09 — `operation_event`, partitioned, with a unique event key

**Goal.** The audit trail, and duplicate-safe observation writes.

**Files.** `0007_operation_event.py` (monthly `PARTITION BY RANGE`, initial partitions, a unique
key over the natural identity of an observation); `tests/db/test_events.py`.

**Review for.**
- Monthly partitions and indefinite retention (D22). The partition-creation job is *not* in this
  PR; the PR should say where it will live, since a silent failure there is invisible for years
  (`99-open-questions.md`).
- The unique key makes a repeated watch event an idempotent upsert (p.13: "watch event duplicated").
  Review what "the same event" means — that definition is the whole protection.
- Events are append-only. No `UPDATE`, no `DELETE` grants.

**Done when.** Inserting the same observed event twice yields one row and no error.

**Size.** ~250 lines.

### 10 — Directory tables and sessions

**Goal.** Storage for the identity snapshot, before any LDAP code exists.

**Files.** `0008_identity.py` — `directory_user` (keyed on the immutable identifier),
`directory_group`, `user_group_membership`, `session`; `tests/db/test_identity.py`.

**Review for.**
- The user's primary key is the immutable identifier; DN and username are display-only columns,
  nullable and freely rewritable (D7).
- `session` is a row with an expiry and a revocation timestamp — opaque, server-side, killable
  (D23). No token signing, anywhere.
- Membership is a snapshot table a query can join locally (D9), including indirect membership
  flattened by the syncer — the schema should not assume `memberOf` is complete.

**Size.** ~200 lines.

### 11 — Policy, projects, clusters

**Goal.** Policy as data (D25), and the registries the planner validates against.

**Files.** `0009_policy_and_registry.py` — `policy` rows per (group, workload type) carrying max
duration, priority ceiling, allowed GPU flavors, quota; `policy_exception`; `project`; `cluster`;
`cluster_flavor`; `tests/db/test_policy_tables.py`.

**Review for.**
- `policy_exception` is a **separate** record naming the approver and the limit exceeded (D11,
  invariant #13). If a reviewer can see any path where an over-limit approval just raises
  `approved_duration`, the PR is wrong.
- `project` is desired state that something else reconciles (D14) — this PR adds no Kubernetes
  behaviour.
- `cluster` and `cluster_flavor` are what step 21 validates a submitter's choice against (D18).

**Invariants.** #13.

**Size.** ~250 lines.

### 12 — `execution_plan` and `operation_attempt`

**Goal.** The immutable plan (p.8) and attempt identity (p.5).

**Files.** `0010_plan_and_attempt.py`; `tests/db/test_plan_immutability.py`.

**Review for.**
- The plan carries target cluster, namespace, queue, priority, GPU flavor and attempt identity —
  the p.8 list.
- Immutability is enforced, not documented: a trigger rejecting `UPDATE`, and a test proving it.
- `operation_attempt` holds `attempt_number` with a unique constraint per operation, and the
  deterministic name `echo-<operation-id>-a<attempt-number>` is **derived**, not stored as free
  text (invariant #5). A generated column or a function is fine; a hand-written string column that
  can disagree with its parts is not.
- A retry gets a **new** plan row, because a retry is re-planned (D16).

**Invariants.** #5.

**Size.** ~250 lines.

---

## Phase 2 — Control plane

### 13 — FastAPI skeleton

**Goal.** A process that boots, connects, and reports health. No domain logic.

**Files.** `echo/api/main.py`, `echo/config.py`, `echo/db/session.py`, `echo/api/errors.py`,
logging setup; `tests/api/test_health.py`.

**Review for.**
- Writer and replica engines are **separate, explicitly named** objects
  (`writer_session`, `replica_session`) so that using the wrong one is visible in a diff
  (invariant #2). Nothing uses the replica yet; the separation exists so it cannot be forgotten.
- `/readyz` checks the writer and the schema version; `/healthz` does not touch the database.
- One error model, so later PRs cannot each invent their own.
- Log configuration explicitly excludes credentials (D7).

**Invariants.** #2.

**Size.** ~250 lines.

### 14 — LDAP bind, login, logout

**Goal.** Authentication (D7) and session issuance (D23).

**Files.** `echo/directory/client.py`, `echo/api/routes/auth.py`, rate limiter;
`tests/api/test_auth.py` with a containerized OpenLDAP (see Q4).

**Review for.**
- LDAPS or StartTLS is **mandatory** and not switchable off by configuration in production. A
  cleartext simple bind must be impossible to configure by accident.
- Bind attempts are rate-limited per account, because a retry loop trips Active Directory lockout
  and locks out real users (D7). Test the limiter.
- The password is never logged, never persisted, never placed in an exception message. Review the
  exception paths specifically — that is where it leaks.
- The session cookie is `HttpOnly`, `Secure`, `SameSite`, and holds an opaque identifier only.
- `owner_subject` is read from `objectGUID` / `entryUUID` (D7), with the fallback behaviour when
  the attribute is missing made explicit — fail closed.

**Size.** ~350 lines.

### 15 — Authentication dependency and group authorization

**Goal.** Turn a session cookie into a user with resolved groups, by local join (D9).

**Files.** `echo/api/deps.py`, `echo/api/authz.py`; `tests/api/test_authz.py`.

**Review for.**
- The session lookup hits the **writer** (invariant #2). An authorization decision from a lagging
  replica is exactly the correctness decision D24 and invariant #2 forbid.
- A revoked or expired session is rejected on the next request, with no cache in front of it (D23).
- Groups come from the local snapshot, never from a live LDAP call on the request path (D9).

**Invariants.** #2.

**Size.** ~200 lines.

### 16 — Directory syncer

**Goal.** The `scanner` entrypoint's first job: mirror users and groups, revoke sessions (D9).

**Files.** `echo/scanner/directory_sync.py`, `echo/scanner/main.py`;
`tests/scanner/test_directory_sync.py`.

**Review for.**
- Nested groups are resolved with `LDAP_MATCHING_RULE_IN_CHAIN` (OID `1.2.840.113556.1.4.1941`) or
  a recursive search. A flat `memberOf` read silently misses indirect membership (D7).
- A user disabled or removed in the directory has their sessions killed in the same run (D9, D23).
- The sync is idempotent and interruptible; a partial run leaves a consistent snapshot.
- The interval is configuration with a documented default, and the PR says the default is a
  placeholder (`99-open-questions.md`).

**Size.** ~300 lines.

### 17 — Policy engine

**Goal.** Resolve (user, groups, workload type, request) into a decision. Pure, no I/O.

**Files.** `echo/policy/resolve.py`, `echo/policy/models.py`; `tests/policy/test_resolve.py`.

**Review for.**
- The function takes policy rows as arguments and returns a decision; the database read is the
  caller's job. That is what makes the rules table-testable.
- `approved_duration` defaults to the policy limit for the group and workload type (D11).
- The decision can say "exceeds limit, requires approval" but can never itself grant beyond the
  limit (D11, invariant #13).
- Platform limits are checked here, before Kueue ever sees the workload (V5). No quota arithmetic
  over "free GPUs" appears anywhere in this file (invariant #8).

**Invariants.** #8, #13.

**Size.** ~300 lines, over half of it a table-driven test.

### 18 — `POST /operations` — Training only

**Goal.** The submission transaction (p.9). The most important PR in phase 2.

**Files.** `echo/api/routes/operations.py`, `echo/api/schemas/training.py`,
`echo/db/submit.py`; `tests/api/test_submit.py`.

**Review for.**
- **One** transaction inserting operation + revision 1 + queue row + event, through the step-06
  create function (invariant #12, D6). Revision 1 comes from this transaction, not from a worker.
- Idempotency is `INSERT ... ON CONFLICT (owner_subject, idempotency_key) DO NOTHING RETURNING`,
  then select and return the original when no row comes back (V1). A retried request returns the
  original operation with the same status code semantics — decide and document which.
- The policy snapshot is written here, once (D10).
- The request carries the **target cluster** and the API rejects a request without one (D18).
- `admitted_at` is untouched (invariant #9).
- Response is `202 Accepted` with the operation ID (p.8).
- If the transaction fails, nothing was submitted — test the failure path, not only the happy one.

**Invariants.** #9, #12.

**Size.** ~350 lines.

### 19 — Read endpoints

**Goal.** `GET /operations`, `GET /operations/{id}`, `GET /operations/{id}/events`.

**Files.** `echo/api/routes/operations.py` (extended), `echo/api/schemas/views.py`;
`tests/api/test_reads.py`.

**Review for.**
- These are reporting reads and use the **replica** session — and the PR states plainly that no
  code path uses their output for a decision (invariant #2). Where a read feeds a subsequent write,
  it goes to the writer instead; name those places.
- The detail view distinguishes desired state from projected runtime state (D24). A user must not
  be able to read a stale `Running` as fact — the response should carry the observation timestamp.
- Authorization filters by group membership, and a user outside the project gets `404`, not `403`.

**Invariants.** #2.

**Size.** ~250 lines.

---

## Phase 3 — Planner

### 20 — Planner entrypoint and claim loop

**Goal.** A process that claims and releases queue rows correctly, doing no planning at all.

**Files.** `echo/planner/main.py`, `echo/db/claim.py`; `tests/planner/test_claim_loop.py`.

**Review for.**
- The claim calls the step-08 SQL function through `session.execute(text(...))`. It is **not**
  expressed through the ORM (D4). If a reviewer sees ORM query construction around the claim,
  reject it.
- Completion checks the returned row count and treats zero as lost lease (invariant #6).
- Polling is the baseline loop. `LISTEN/NOTIFY` is added in step 25 as a wake-up hint and the
  polling path stays (invariant #4).
- Backoff pushes `available_at` forward with jitter and a cap (V3); it does not `sleep` inside a
  held transaction.
- No leader election, no singleton (D4). Two planner replicas in a test must not collide.

**Invariants.** #4, #6.

**Size.** ~300 lines.

### 21 — Validation and the immutable plan

**Goal.** `Queued → Planning → Dispatching`, with a plan row written once (p.8, D18).

**Files.** `echo/planner/plan.py`; `tests/planner/test_planning.py`.

**Review for.**
- The submitter's cluster choice is **validated, not chosen** (D18, invariant #8). Any code that
  ranks, scores or compares clusters is the deviation this decision exists to prevent.
- Nothing counts free GPUs, anywhere, in any form — including "capacity remaining" derived from
  usage tables (invariant #8).
- The plan is written through the step-12 immutable path and includes the attempt identity.
- Failure to validate transitions to `Rejected` with a reason, via the step-06 function.
- The queue row is re-armed or set idle by the fenced completion, in a transaction that closes
  before anything else happens.

**Invariants.** #8.

**Size.** ~300 lines.

### 22 — Intake validation and the rejection path

**Goal.** Settle where `Submitted → Validating → Rejected|Queued` happens, and implement it.

**Files.** `echo/api/validate.py` or `echo/planner/validate.py` per the answer to Q2;
`tests/*/test_validation.py`.

**Review for.**
- The split is written down: schema, authorization and required-field validation synchronous in
  the API; policy and placement validation in the planner (D18). Whichever way Q2 lands, the rule
  belongs in `06-decisions.md` as a new decision, and this PR should add it.
- A rejection is a state transition with a reason and an event, never a bare HTTP error with no
  durable record — a rejected operation must be visible in the audit trail.
- A submitted image is **not** inspected or fetched (invariant #16, D27). Image validity surfaces
  at runtime as a non-retryable failure (D17). See Q6 if we want a registry pre-check.

**Invariants.** #16.

**Size.** ~250 lines.

---

## Phase 4 — The worker, and the Training slice

### 23 — Go worker skeleton and cluster registry

**Goal.** A worker that boots, gates on schema version, holds N cluster clients, and shuts down
cleanly.

**Files.** `worker/cmd/compute-worker/main.go`, `worker/internal/clusters/registry.go`,
`worker/internal/db/db.go`, health endpoints; `worker/internal/clusters/registry_test.go`.

**Review for.**
- The worker **refuses to boot** if `schema_meta` is older than it requires (D5). Test that it
  exits non-zero, rather than logging and continuing.
- The registry holds one controller-runtime manager per cluster in one process, because workers are
  cluster-agnostic (D8). One entry is enough for the slice; the shape must already be N.
- Per-manager isolation is at least *structurally* possible — bounded work queues per cluster — and
  the PR names it as unresolved (`99-open-questions.md`).
- The worker runs no migrations, ever (D5).
- Credentials are read from Secrets per cluster (D19) and never logged.

**Size.** ~350 lines.

### 24 — CRD types and manifests

**Goal.** `EchoOperation` and `EchoExecution` as Go types, generated code, CRDs and RBAC.

**Files.** `worker/api/v1alpha1/{echooperation,echoexecution}_types.go`, `zz_generated.*`,
`deploy/crds/*.yaml`, `deploy/rbac/echo-compute-controller.yaml`;
`worker/api/v1alpha1/*_test.go`.

**Review for.**
- Group and version are `operations.echo.erez.io/v1alpha1`, labels are `echo.erez.io/*` — one
  domain, one prefix (p.5, D12).
- `EchoExecution` reproduces the p.5 example's field names exactly: `operationID`,
  `operationGeneration`, `type`, `desiredState`, `queue`, `priorityClass`, `resources.gpu.{flavor,count}`,
  `workload`.
- `EchoOperation.status` matches D26's shape: `phase`, `placement.{generation,cluster,nodeName,nodeUID}`,
  `currentExecution.name`, `conditions`.
- `workload` is documented as **type-dependent** (D27): the submitter's image for Training, E.C.H.O's
  runtime for Inference. A reader of the type must not conclude `image` is always the user's.
- The compute identity's RBAC holds **no verbs** in `*-inference` namespaces (D12). Review the
  RoleBinding scope, not just the verbs.
- Status subresource is enabled, and every status field is documented as observed — the CR is a
  derived snapshot, not the record (invariant #1).

**Invariants.** #1.

**Size.** ~400 lines, most of it generated. Review the hand-written types; skim the generated code.

### 25 — Worker queue consumer

**Goal.** The Go half of the claim protocol, with no Kubernetes calls at all.

**Files.** `worker/internal/queue/{claim.go,lease.go,notify.go}`; `worker/internal/queue/*_test.go`
against the real migrated database.

**Review for.**
- The same SQL functions as the planner (step 08). Two implementations of the claim is the failure
  this shared definition prevents — check that Go did not hand-roll its own copy.
- Lease renewal exists and is safe: a renewal after loss must not resurrect the claim.
- `LISTEN/NOTIFY` wakes the loop; the polling loop remains and a test proves work is still picked
  up with notifications disabled (invariant #4).
- Zero rows on completion → discard the result, log loudly, do not retry the write (invariant #6).
- The transaction is opened, used and closed with no Kubernetes client in scope (invariant #3).
  This is where the pattern is set for every later worker PR.

**Invariants.** #3, #4, #6.

**Size.** ~350 lines.

### 26 — Server-Side Apply of `EchoOperation` and `EchoExecution`

**Goal.** Materialize the plan as CRs, and record the `Dispatching` milestone.

**Files.** `worker/internal/reconcile/apply.go`; envtest-based tests.

**Review for.**
- Names are derived deterministically: `echo-<operation-id>` and
  `echo-<operation-id>-a<attempt-number>` (invariant #5). A second worker applying the same plan
  must produce byte-identical objects.
- Server-Side Apply with an explicit, stable field manager and owned-field set (invariant #11). No
  `Update`, no `Patch` with merge semantics, no read-modify-write.
- `EchoOperation` owns `EchoExecution` via an owner reference, so garbage collection cascades (D26).
- The `Dispatching` record is written **after** the apply returns and in its own transaction
  (invariant #3), and it is a milestone — nothing later branches on it (invariant #14).
- Re-applying an existing object is a no-op that does not bump generation.

**Invariants.** #3, #5, #11, #14.

**Size.** ~350 lines.

### 27 — `EchoExecution` controller: the Training Job

**Goal.** Reconcile an `EchoExecution` of type Training into a `batch/v1` Job under Kueue.

**Files.** `worker/internal/controller/echoexecution_controller.go`,
`worker/internal/workload/training.go`; envtest tests plus a kind-cluster smoke test (Q5).

**Review for.**
- `batch/v1` Job, suspended for Kueue, carrying the LocalQueue label (V4, D15). The queue name
  comes from the plan, not from string assembly in the controller.
- Namespace selection is by **label**, never by parsing `echo-<project>-<type>` (D12). Grep the PR
  for string splitting on namespace names.
- Owner references chain Job → `EchoExecution` → `EchoOperation` (D26).
- The controller derives its action from **observed** cluster state every pass; there is no branch
  on a milestone record (invariant #14). A Job deleted behind the controller's back must be
  recreated on the next pass, and a test should delete it and prove that.
- The user's image is passed through opaquely — not parsed, not rewritten, not inspected
  (invariant #16, D27).
- Requeue is rate-limited with backoff on Kubernetes API errors (p.13, V3).

**Invariants.** #11, #14, #16.

**Size.** ~400 lines.

### 28 — Admission observation starts the expiry clock

**Goal.** `Waiting for Admission → Starting`, and `expires_at = admitted_at + approved_duration`.

**Files.** `worker/internal/observe/admission.go`, `0011_admission_fn.py` if the transition needs a
dedicated function; tests in both languages.

**Review for.**
- `admitted_at` is set from the **observed Kueue admission**, not from submission and not from the
  worker's wall clock at claim time (invariant #9, p.12).
- `expires_at` is computed once, in the database, in the same statement.
- The write carries the fencing token (invariant #6) and an expected version, so a later stale
  writer cannot recompute it (invariant #9).
- Kueue is the authority for admission; the worker records what it saw and never admits anything
  itself (invariant #8).

**Invariants.** #6, #8, #9.

**Size.** ~300 lines.

### 29 — Running observation

**Goal.** Project Pod and Job status into PostgreSQL as `Running`.

**Files.** `worker/internal/observe/running.go`; tests.

**Review for.**
- The write is an idempotent upsert keyed on the step-09 unique event key, so a duplicated watch
  event is harmless (p.13).
- The projection is labelled as a projection, with an observation timestamp (D24, invariant #1).
- Fencing on every write (invariant #6).
- The observer does not write desired state. Only observed columns move.

**Invariants.** #1, #6.

**Size.** ~250 lines.

### 30 — Terminal observation and failure classification

**Goal.** `Succeeded` / `Failed`, with a reason that decides retryability (D17).

**Files.** `worker/internal/observe/terminal.go`, `worker/internal/classify/reason.go`;
table-driven tests over the D17 matrix.

**Review for.**
- The classifier's table is the D17 list, split retryable / non-retryable, with an explicit
  `Unknown → do not retry` default. A test row per line of D17.
- Deterministic OOM is distinguished from co-tenancy OOM using the request/limit comparison, not
  bare `OOMKilled` (D17). If we cannot do that confidently yet, the PR must classify it `Unknown`
  rather than guess — and say so.
- `PlacementFailed` is a reason on `Failed`, not a new state (D20, invariant #7).
- The terminal write goes through the step-06 function and cannot be moved afterwards
  (invariant #7).
- Usage finalization is a stub here with a named follow-up (D21), not silently omitted.

**Invariants.** #6, #7.

**Size.** ~350 lines.

### 31 — The retry transaction

**Goal.** `Failed(retryable) → Retrying → Queued`, with a new attempt and a new plan.

**Files.** `0012_retry_fn.py` (`echo_operation_retry()`), `worker/internal/retry/retry.go`;
tests in both languages.

**Review for.**
- **One** transaction increments `attempt_number`, writes the attempt row, re-arms the queue and
  records the event (invariant #5). The worker calls it; the worker does not compose it from
  several statements.
- The worker cannot create an attempt any other way. Search the PR for any other write to
  `operation_attempt`.
- The retry returns to `Queued` so it is re-planned and re-admitted (D16), and validates the
  **same** cluster (D18) — which is what keeps a retry from wandering away from its PVC.
- `placement.generation` is untouched (four-counters table).
- `max_attempts` is configuration per workload type with a placeholder default, flagged as unset
  (`99-open-questions.md`).
- Backoff via `available_at` with jitter (V3).

**Invariants.** #5.

**Size.** ~300 lines.

### 32 — Periodic full reconciliation and adoption

**Goal.** Survive a missed watch event and a worker crash mid-creation (p.13).

**Files.** `worker/internal/reconcile/resync.go`; tests that delete objects and kill workers.

**Review for.**
- A full resync runs on an interval regardless of watch health (p.13: "watch event missed").
- A replacement worker **adopts** the existing deterministically named object rather than creating
  a new attempt (p.13, invariant #5). The test: kill the worker between apply and completion, start
  another, assert one Job and one attempt.
- A hand-edited owned field is corrected by SSA on the next pass (invariant #11) — test by editing
  a field and waiting.
- Reconciliation is driven by observed state, with milestones read-only (invariant #14).

**Invariants.** #5, #11, #14.

**Size.** ~300 lines.

---

## Phase 5 — Cancellation, expiry, extension

### 33 — `POST /operations/{id}/cancel`

**Goal.** Cancellation as a change of durable intent (invariant #10, p.12).

**Files.** `echo/api/routes/operations.py`, `echo/db/cancel.py`; tests.

**Review for.**
- One transaction: new revision, `desired_generation` incremented, state → `Cancelling`, queue row
  re-armed, event written.
- Cancel is accepted from **any** non-terminal state (p.7) and is a no-op — not an error — on an
  already-terminal operation.
- No Kubernetes call from the API. Ever. The API's reach ends at the writer (D1).

**Invariants.** #10, #12.

**Size.** ~250 lines.

### 34 — The worker honours cancellation

**Goal.** `Cancelling → Cancelled`, with the workload removed.

**Files.** `worker/internal/reconcile/cancel.go`; tests including the race.

**Review for.**
- The worker reads the **newest** desired state at the start of every pass, so a delayed `START`
  cannot revive a cancelled workload (invariant #10). Test exactly that ordering.
- Deletion relies on owner-reference cascade from `EchoOperation` (D26) rather than deleting each
  object by hand.
- The cancel-races-startup case resolves by desired generation, not by timing (p.13).
- `Cancelled` is terminal and set through the step-06 function (invariant #7).

**Invariants.** #7, #10.

**Size.** ~300 lines.

### 35 — Expiry scanner

**Goal.** Time-bound operations expire, and a stale scanner cannot undo an extension.

**Files.** `echo/scanner/expiry.py`, `0013_expire_fn.py`; tests including the stale-scanner race.

**Review for.**
- The expire statement carries an **expected database version** and updates zero rows if the
  operation was extended in the meantime (invariant #9, p.12). Test: extend, then let an old
  scanner run, assert nothing changed.
- Expiry is driven by `expires_at`, which came from admission, not submission (invariant #9).
- `Expired` is terminal (invariant #7).
- The scanner claims with the same `SKIP LOCKED` pattern and no leader election (D4).
- Research PVC retention (`Compute expires → Pod removed → PVC retained 7 days → PVC deleted`,
  p.11) is **out of scope** — Training has no PVC in this slice. Say so in the PR.

**Invariants.** #7, #9.

**Size.** ~300 lines.

### 36 — Extension, and the policy exception

**Goal.** `POST /operations/{id}/extend`, re-evaluated against current policy (D10, D11).

**Files.** `echo/api/routes/operations.py`, `echo/policy/extend.py`; tests.

**Review for.**
- The extension is checked against policy and group membership **as they stand now**, not against
  the submission snapshot (D10). A user who left the team cannot keep extending.
- The decision is recorded as an `operation_event`, not as a revised policy snapshot (D10).
- An approval beyond the policy limit writes a `policy_exception` naming the approver and the limit
  exceeded, and `approved_duration` records that it came from an exception (invariant #13, D11).
  Reject any path that folds the excess in silently.
- A denial carries a reason the UI can show (D10's stated cost).
- The write uses the expected-version path so it cannot race step 35 (invariant #9).

**Invariants.** #9, #13.

**Size.** ~300 lines.

---

## Phase 6 — Minimal UI

The slice is not usable without these, but none of them holds a correctness invariant except #2.

### 37 — Vite build served by the API, and login

**Files.** `web/` app shell and login page, `echo/api/static.py`, build wiring in CI.

**Review for.** Static assets served by the API process, one origin, no CORS configuration
anywhere (D2). The UI calls only the API and has no database client — that is what makes
invariant #2 structural rather than remembered.

**Invariants.** #2.

**Size.** ~300 lines.

### 38 — Submit a training job, and list operations

**Review for.** The cluster is an explicit, required choice, and the form shows per-cluster capacity
from the PostgreSQL projection — without it the choice is a guess (D18, D2). Capacity shown is a
projection and must be labelled as one; it is not a free-GPU count (invariant #8).

**Invariants.** #8.

**Size.** ~350 lines.

### 39 — Operation detail: lifecycle, events, milestones

**Review for.** Desired state and observed state are visually distinct, with the observation
timestamp shown (D24). Milestones appear in a detail view — that is the only place D16's continuous
recording is legible to an operator, which D16 lists as a cost.

**Size.** ~300 lines.

---

## Phase 7 — After the slice

Sketches, not specifications. Each needs its own breakdown when we get there, and several are
blocked on `99-open-questions.md`.

| # | Step | Blocked on |
| --- | --- | --- |
| 40 | Project provisioning controller: `project` row → namespaces, RoleBindings, ResourceQuota, NetworkPolicy, LocalQueue, via `echo-project-controller` (D14) | PodSecurity admission level for BYO-image namespaces |
| 41 | Usage aggregation: Prometheus → PostgreSQL buckets, allocated and observed kept separate (D21) | Aggregation interval |
| 42 | Reporting on read replicas, with a lint rule that keeps decision paths off them (invariant #2) | — |
| 43 | Research workload type: StatefulSet or interactive Pod, Service, PVC, time-bound lease, PVC retention (p.11) | Preemptible priority class for research |
| 44 | Kueue configuration as code: ClusterQueues, cohorts, borrowing and preemption limits (D15) | Borrowing and preemption limits |
| 45 | Admin policy UI (D25), approval workflow for D11 exceptions | — |
| 46 | Partition maintenance job and archive destination (D22) | Archive destination, alerting owner |
| 47 | Managed inference: runtime image build pipeline, version catalog, vLLM config validator (D27) | Four open questions in `99-open-questions.md` |
| 48 | Inference placement: observe-and-pin, `EchoOperation.status.placement`, `HoldPlacement` / `Relocate`, `Unavailable` and `Relocating` (D20, D26) | Default recovery policy; `Unavailable` confirmed inference-only |
| 49 | Stable inference endpoint: Service and route owned by `EchoOperation`, DNS scheme (D26, D27) | Endpoint naming and DNS scheme |
| 50 | Alerting on long-lived `PinnedNodeUnavailable` (D26) | — |

---

## Invariant coverage

Every invariant in `CLAUDE.md` should be *enforced* by a specific step and *tested* there. This
table is the audit; a gap in it is a gap in the plan.

| Invariant | Enforced in | Tested by |
| --- | --- | --- |
| #1 PostgreSQL is the source of truth | 24, 29 | CR documented as derived; projection carries observation time |
| #2 Replicas never used for correctness | 13, 15, 19, 37 | Separate named sessions; session lookup on writer |
| #3 No open transaction across a Kubernetes call | 25, 26, 28 | Transaction scope has no client in scope |
| #4 `LISTEN/NOTIFY` is a hint | 07, 08, 20, 25 | Work is picked up with notifications disabled |
| #5 Workers never invent an attempt | 12, 26, 31, 32 | Kill-mid-apply test yields one attempt |
| #6 Every completion is fenced | 08, 25, 28, 29, 30 | Stale writer updates zero rows |
| #7 Terminal states never move backward | 04, 06, 30, 34, 35 | Ad-hoc `UPDATE` rejected by trigger |
| #8 Never counts free GPUs | 17, 21, 28, 38 | No capacity arithmetic; validation only |
| #9 Clock starts at admission | 05, 28, 35, 36 | Extend-then-stale-scanner test |
| #10 Cancellation changes durable intent | 33, 34 | Delayed START does not revive |
| #11 Server-Side Apply with field ownership | 24, 26, 27, 32 | Hand-edited field is corrected |
| #12 Submission is one transaction + idempotency key | 05, 18 | Duplicate key returns the original |
| #13 Over-limit approval is an exception record | 11, 17, 36 | No path folds excess into `approved_duration` |
| #14 Milestones do not drive control flow | 26, 27, 32 | Object deleted behind the controller is recreated |
| #15 Never selects a node | 21 (and 48) | No node selection in the planner |
| #16 No Kubernetes access for managed inference; images opaque | 22, 27 (and 47) | Image passed through unparsed |

---

## Questions before step 01

Answers to Q1–Q3 change the plan itself; Q4–Q8 change how a step is built, and I can proceed with
the stated assumption if you would rather not decide now.

**Q1 — Repository layout.** Is the four-directory monorepo above right, and is `echo` the Python
package name? Everything below step 01 cites those paths, so renaming later is a wide diff.
*Assumption if unanswered: as written.*

**Q2 — Who owns intake validation?** `Submitted → Validating → Rejected|Queued` is one lifecycle
stage but D18 puts placement validation in the planner. My proposal: schema, authorization and
required fields synchronously in the API; policy and placement in the planner. This is a new
decision either way and belongs in `06-decisions.md`, which is why it is step 22 rather than a
detail. *Assumption if unanswered: the split above.*

**Q3 — Does step 06 wait for step 09?** The transition function wants to write `operation_event`,
which does not exist until step 09. I would rather reorder — 09 before 06 — than merge a function
with a hole in it. Any objection to that reordering?

**Q4 — Is there a test directory?** Step 14 needs something to bind against. A containerized
OpenLDAP in CI is my default. A dev-only stub behind the same interface is faster but means the
nested-group path (`LDAP_MATCHING_RULE_IN_CHAIN`) is never exercised until staging — and that path
is the one D7 warns about. *Assumption if unanswered: containerized OpenLDAP, and it must exercise
nested groups.*

**Q5 — What Kubernetes is available in CI?** envtest covers the API server but has no scheduler and
no Kueue, so steps 27 and 28 can only be *fully* tested on a real cluster with Kueue installed. Is
a kind cluster with Kueue available to CI, or do those steps carry a manual verification note?
*Assumption if unanswered: envtest in CI, kind + Kueue run manually, and the PR says which
assertions are manual.*

**Q6 — Registry pre-check on submit?** `99-open-questions.md` leaves this open and step 22 assumes
**no** pre-check, so an invalid image surfaces at runtime as a non-retryable failure (D17). A
pre-check fails faster but couples admission to registry availability. Fine to defer, but step 22
is where the decision becomes visible.

**Q7 — One cluster or several in the slice?** D8 says multi-cluster from day one, and step 23 builds
the N-manager registry shape. I plan to run the Training slice against a **single registered
cluster** so the slice is not gated on per-manager isolation
(`99-open-questions.md`). Confirm that is acceptable.

**Q8 — Placeholder values.** Lease duration 30s (V2), and `max_attempts`, sync interval, resync
interval, placement deadline and session lifetime all unset in
`99-open-questions.md`. I intend to make each configuration with a documented placeholder default
and a comment saying it is unmeasured. Do you want them collected in one config module so they are
easy to find and revisit, rather than spread across the steps that need them?
