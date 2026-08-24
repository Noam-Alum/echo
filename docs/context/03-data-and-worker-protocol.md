# Data and worker protocol

_Source: `docs/specification/E.C.H.O.pdf`, pp. 8–10._

This is the mechanical core of the system. The SQL below is reproduced verbatim from the
specification so future migrations and queries can be diffed against it.

## Submission transaction

The API performs one PostgreSQL transaction, for example:

```sql
BEGIN;

INSERT INTO compute_operation (...);

INSERT INTO operation_revision (...);

INSERT INTO reconcile_queue (...);

INSERT INTO operation_event (...);

COMMIT;
```

If the transaction fails, nothing was submitted.

Require a client idempotency key:

```sql
UNIQUE (owner_subject, idempotency_key)
```

A retried HTTP request returns the original operation instead of creating a duplicate.

_Spec p.9_

## Safe worker claiming

Workers claim on the PostgreSQL writer, for example:

```sql
WITH candidate AS (
    SELECT operation_id
    FROM reconcile_queue
    WHERE state = 'pending'
      AND available_at <= clock_timestamp()
    ORDER BY priority DESC, available_at
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
UPDATE reconcile_queue AS queue
SET state = 'claimed',
    lease_owner = $1,
    lease_until = clock_timestamp() + interval '30 seconds',
    claim_epoch = claim_epoch + 1
FROM candidate
WHERE queue.operation_id = candidate.operation_id
RETURNING queue.*;
```

`SKIP LOCKED` allows competing queue consumers without selecting the same row.

Every completion update includes the fencing token:

```sql
UPDATE reconcile_queue
SET processed_generation = $processed_generation,
    state = CASE
        WHEN desired_generation > $processed_generation
            THEN 'pending'
        ELSE 'idle'
    END,
    lease_owner = NULL,
    lease_until = NULL
WHERE operation_id = $operation_id
  AND lease_owner = $worker_id
  AND claim_epoch = $claim_epoch;
```

Zero updated rows means the worker lost its lease and must discard its result.

Never hold a PostgreSQL transaction open while calling Kubernetes.
`LISTEN/NOTIFY` can wake workers quickly, but polling remains the reliable fallback.
**Notifications are not the durable queue.**

_Spec p.10_

## Four counters, and why they are not the same thing

**Derived.** The specification never states this in one place, and conflating any two of these breaks a
different invariant. It is the easiest thing in the design to get wrong.

| Counter | Increments when | Where it lives | What it is for |
| --- | --- | --- | --- |
| `desired_generation` (a new `operation_revision`) | The **user or policy changes intent** — submit, cancel, extend, resize an inference deployment | `compute_operation` / `operation_revision` / `reconcile_queue` | Tells a worker its in-flight work is stale, and re-arms the queue row |
| `claim_epoch` | A **worker claims the row**, including a re-claim after a lease expires | `reconcile_queue` | Fencing — makes a stalled worker's late write match zero rows |
| `attempt_number` | A **previous physical execution failed** and is retried | Attempt identity, CR name | Distinguishes physical Kubernetes executions |
| `placement.generation` | A `Relocate` operation's binding **moves to a new node** | `EchoOperation.status.placement` (D26) | Marks an epoch of observed node binding |

A worker stalling and being replaced bumps **only `claim_epoch`**. It creates neither a revision — desired
state did not change — nor a new attempt, because p.13's "worker crashes after K8s creation → next worker
finds deterministic object" requires the replacement to **adopt** the existing Job. That is what the
deterministic `echo-<operation-id>-a<attempt-number>` name is for, and why p.5 forbids a worker inventing
an attempt.

Revision 1 exists before any worker sees the operation: the p.9 submission transaction inserts
`operation_revision` itself.

**Many attempts live under one placement generation.** `attempt_number` increments without touching
`placement.generation` — that asymmetry is exactly what lets an inference model's pinned node survive
attempt replacement (D26). Under `HoldPlacement` the generation never increments at all; only a
`Relocate` recovery moves it. Note also the different owners: the first three counters live in
PostgreSQL, which owns desired state and the queue, while `placement.generation` lives in
`EchoOperation.status`, because an observed node binding is runtime state that Kubernetes owns (p.2).

## Where the lifecycle is enforced

**D6:** the operation state machine lives in PostgreSQL — a transition function is the write path both
languages call, and a trigger is the backstop so an ad-hoc `UPDATE` cannot move a terminal state
backward. The function needs a *create* path as well, because the submission transaction above is itself
an entry point into the state machine. Anyone writing a migration should start here.

**D16:** orchestration milestones (validation, planning, workload creation, admission, startup) are also
recorded in PostgreSQL and projected into `EchoExecution.status` for `kubectl` visibility. They are for
audit and observability — **observed state drives the next action**, never the milestone record.

## Schema surface named by the specification

**Derived:** this is an inventory of identifiers the specification's examples actually mention — not a
schema. Types, keys, nullability and the rest are undefined so far.

Tables:

| Table | Role in the specification |
| --- | --- |
| `compute_operation` | The submitted operation |
| `operation_revision` | Revision of desired state |
| `reconcile_queue` | Durable work queue the workers claim from |
| `operation_event` | Audit/event trail |

Columns:

| Column | Appears in | Notes |
| --- | --- | --- |
| `operation_id` | `reconcile_queue` | Claim/complete key |
| `state` | `reconcile_queue` | Observed values: `'pending'`, `'claimed'`, `'idle'` |
| `available_at` | `reconcile_queue` | Claim eligibility and ordering |
| `priority` | `reconcile_queue` | `ORDER BY priority DESC, available_at` |
| `lease_owner` | `reconcile_queue` | Fencing token, part 1 |
| `lease_until` | `reconcile_queue` | 30-second lease in the example |
| `claim_epoch` | `reconcile_queue` | Fencing token, part 2; incremented per claim |
| `desired_generation` | `reconcile_queue` | Compared on completion to re-arm the row |
| `processed_generation` | `reconcile_queue` | Written on completion |
| `owner_subject` | submission | Idempotency scope (an OIDC subject) |
| `idempotency_key` | submission | Client-supplied |
| `admitted_at` | time-bound operations | Start of the expiry clock |
| `expires_at` | time-bound operations | `admitted_at + approved_duration` |
| `approved_duration` | time-bound operations | From the approval/policy path |

## End-to-end submission and execution

Transcribed from the page-8 sequence diagram, titled *Job to GPU Execution — PostgreSQL-centric
orchestration with immutable planning and Kubernetes-native admission*.

Participants: `User` (OIDC subject) · `E.C.H.O API` (auth, commands) · `PostgreSQL Writer` (intent,
queue, events) · `E.C.H.O Planner` (policy, placement) · `E.C.H.O Worker` (reconciler) ·
`Kubernetes API` (desired, observed) · `Kueue` (quota admission) · `Kube Scheduler` (node placement) ·
`GPU Pod` (execution).

**Request contract:** OIDC identity verified · project and policy resolved · idempotency key scoped to
the submitter.

> **Superseded by D7.** Authentication is LDAP only. The verified-identity and scoped-idempotency
> intent is preserved, but there is no OIDC subject — `owner_subject` holds an immutable directory
> identifier (`objectGUID` / `entryUUID`), and the API issues its own revocable session.

**01 · Submission**

1. User → API: `POST /operations` + `Idempotency-Key`.
2. API → API: authenticate and authorize groups.
3. API → PostgreSQL Writer: insert operation + queue + event.
4. PostgreSQL Writer → API: commit operation ID.
5. API → User: `202 Accepted`.

**02 · Immutable planning**

6. Planner → PostgreSQL Writer: claim unplanned operation.
7. Planner → Planner: validate policy and select target.
8. Planner → PostgreSQL Writer: store immutable execution plan.

> **Execution plan:** target cluster · namespace · queue · priority · GPU flavor · attempt identity.
> The plan *ranks* placement; Kueue remains the final authority for quota admission.

**03 · Reconciliation**

9. Worker → PostgreSQL Writer: claim reconcile row.
10. Worker → PostgreSQL Writer: read latest desired generation.
11. Worker → Kubernetes API: apply `EchoExecution`.
12. Worker → PostgreSQL Writer: record `Dispatching`.

**04 · Kubernetes GPU execution**

13. Worker → Kubernetes API: apply workload owned by `EchoExecution`.
14. Kubernetes API → Kueue: workload waits for quota.
15. Kueue → Kubernetes API: reserve quota and admit.
16. Kubernetes API → Kube Scheduler: schedule admitted Pods.
17. Kube Scheduler → GPU Pod: bind to GPU node.

**05 · Observation and finalization**

18. Worker → Kubernetes API: observe execution.
19. Worker → PostgreSQL Writer: upsert `Running` observation.
20. GPU Pod → Kubernetes API: completion or failure.
21. Worker → Kubernetes API: observe terminal state.
22. Worker → PostgreSQL Writer: record result and usage.

**Postcondition:** terminal outcome persisted · usage finalized · cleanup or retention continues
under policy.

Diagram legend: command · reconcile · admission · return/observation.

_Spec p.8 (diagram)_
