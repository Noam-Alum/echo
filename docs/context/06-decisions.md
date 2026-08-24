# Architecture decisions

Decisions taken **2026-08-24**, answering the questions the specification deliberately leaves open.
Each entry records what was chosen, why, and what it trades away — an entry with no downside listed is
under-examined.

These are decisions *about* the specification, never silent overrides of it. Where a decision departs
from specification text or a specification diagram, it is marked **Deviation** and says so plainly.

Deviations recorded here: **D7** (LDAP replaces OIDC), **D16** (`Checkpointing` removed as a lifecycle
phase), **D18** (the planner validates placement rather than ranking it), **D26** (`Unavailable` and
`Relocating` added to the lifecycle).

## Implementation shape

### D1 — Language boundary: Python control plane, Go worker

**Decision.** Python (FastAPI) for the API, planner, policy engine and expiry/recovery scanner. Go for
the worker pool.

**Why.** Per the p.3 architecture diagram, every control-plane arrow terminates at the PG writer —
none of those components touch Kubernetes. The worker is the only Kubernetes-facing component, and
that is where Go is hard to replace: controller-runtime has no Python equivalent (Python's
`watch.Watch()` is a raw stream, so shared informer caching, resync and rate-limited requeue would be
hand-rolled — and the spec depends on exactly those for "watch event missed → periodic full
reconciliation" and "Kubernetes API unavailable → backoff", p.13), Server-Side Apply field ownership
is compile-time checked through generated `applyconfigurations`, and Kueue, JobSet, RayJob and
Kubeflow all ship Go APIs first. The spec already names the worker as Go (p.4).

The split is cheap because **PostgreSQL is already the integration contract** — no RPC surface to
version, no shared client library. The schema is the interface.

**Trades away.** Two toolchains and two dependency streams. The lifecycle state machine would exist in
two languages, which is why D6 puts it in the database instead.

_Spec p.3, p.4_

### D2 — Web UI: React + TypeScript + Vite, served by the API

**Decision.** Built to static assets and served by the API process.

**Why.** One origin, no CORS, one deployable. The UI talks only to the API and never to PostgreSQL
directly, which makes invariant #2 structurally enforced rather than a rule to remember.

**Consequence.** D18 requires the UI to show per-cluster capacity, built from PostgreSQL projections —
without it, "explicit cluster choice" is a guess.

**Trades away.** UI releases couple to API releases. No SSR, so first paint on heavy dashboards is
slower than a Next.js equivalent.

_Spec p.3_

### D3 — The worker reconciles the `EchoExecution` CR

**Decision.** The worker applies the CR *and* reconciles it into the Job / Deployment / StatefulSet,
via controller-runtime. No separate operator.

**Why.** Matches the p.8 sequence, where the worker both applies and observes. One actor owns the
chain, so there is one reconcile loop to debug and no second system to keep in sync.

**Trades away.** Nothing continues reconciling CRs if the worker pool is down.

_Spec p.8_

### D4 — Control plane: one codebase, three entrypoints

**Decision.** One Python package with `api`, `planner` and `scanner` entrypoints, deployed as separate
Deployments with independent replica counts.

**Why.** Planner and scanner claim rows with `FOR UPDATE SKIP LOCKED` exactly as the worker does
(p.8: "claim unplanned operation"), so all three scale horizontally with no leader election and no
singleton to protect.

**Consequences.** The Python side needs the claiming CTE, so `session.execute(text(...))` for the
queue and the ORM for ordinary reads and writes. Do not try to express the claim through the ORM.

_Spec p.3, p.8_

### D5 — Migrations: Alembic runner, hand-written migrations, `alembic check` in CI

**Decision.** SQLAlchemy models are the API's query and session layer, not the schema's author.
Migrations are written by hand. `alembic check` fails CI when models drift from the real schema.

**Why.** Follows from D6. Once the lifecycle protocol lives in the database, the schema contains real
logic — a transition function, a trigger, partial indexes on the queue hot path, the fencing columns'
semantics — none of which SQLAlchemy models can represent. Autogenerate would ignore those objects or
keep proposing to drop them, so migrations would be hand-patched anyway; authoring them deliberately
is the same work without the surprise.

**Consequences.** The Python control plane owns running migrations; the Go worker never does. The
worker must check a schema version at startup and refuse to boot if it is too old, or a worker can
deploy ahead of its migration. Go's queries must be tested against a real migrated database in CI —
that, not DDL authorship, is what actually protects the cross-language boundary.

**Trades away.** No `--autogenerate` convenience for routine column additions.

### D6 — The operation lifecycle is enforced in PostgreSQL

**Decision.** A transition function is the write path both languages call. A trigger is the backstop,
so no ad-hoc `UPDATE` can move a terminal state backward. The function needs a *create* path as well
as a transition path, because the p.9 submission transaction is itself an entry point into the state
machine.

**Why.** The specification already expresses the lifecycle protocol as SQL, not application logic:
queue transitions are a statement (p.10), the re-arm decision is a `CASE` inside the UPDATE (p.10),
fencing is a `WHERE` clause (p.10), extension "must use an expected database version" (p.12), and
uniqueness of intent is a constraint (p.9). The p.2 ownership table puts both desired state and the
reconciliation queue on the PostgreSQL writer. Application-level enforcement would also mean two
implementations after D1.

**Trades away.** PL/pgSQL to maintain and review, and state-machine logic harder to unit test than a
Python class.

_Spec p.2, p.9, p.10, p.12_

## Identity, users and policy

### D7 — Authentication is LDAP only; E.C.H.O issues its own session

**Decision.** LDAP exclusively — a hard deployment constraint, no OIDC. The API binds against the
directory at login and issues its own session credential.

> **Deviation.** p.8's request contract says "OIDC identity verified" and labels the user participant
> "OIDC subject". That is not achievable here. The specification's *intent* — a verified identity,
> resolved groups, and an idempotency key scoped to the submitter — is preserved; only the mechanism
> differs.

**Why LDAP forces this shape.** LDAP has no portable signed assertion — it offers bind and search.
There is no token to validate per request, so the API must own sessions. Per-request binds would
hammer the directory and make logout meaningless.

**Consequences**, each a decision in its own right:

- `owner_subject` holds **`objectGUID`** (Active Directory) or **`entryUUID`** (OpenLDAP) — both
  immutable. **Not** the DN, which changes when a user moves OU, and **not** `sAMAccountName` / `uid`,
  which can be renamed and reused. `owner_subject` is half the idempotency key (p.9), so an unstable
  value there silently breaks idempotency for a renamed user. DN and username are display-only.
- LDAPS or StartTLS is mandatory. Simple bind is cleartext.
- Bind attempts must be rate-limited. A retry loop against Active Directory will trip account lockout
  policy and lock out real users — the failure mode people actually hit in production.
- AD nested groups need `LDAP_MATCHING_RULE_IN_CHAIN` (OID `1.2.840.113556.1.4.1941`) or a recursive
  search. A flat `memberOf` read misses indirect membership.
- The API becomes a credential-handling surface: never log, never persist, TLS throughout, short
  sessions (D23).

**Trades away.** Single sign-on, and delegating credential handling to a dedicated identity provider.

_Spec p.8, p.9_

### D9 — Directory sync: periodic mirror plus refresh on login

**Decision.** A syncer mirrors users and group membership into PostgreSQL on an interval. A user's
groups are re-resolved at login.

**Why.** Submissions never block on the directory, and authorization becomes a local join — which is
what p.2 means by putting the user, group and policy snapshot in PostgreSQL. With D7 there is no token
claim carrying groups, so they must be fetched somewhere; fetching on the submission path would make
an LDAP outage stop all submissions.

**Consequence.** The sync is also the revocation trigger for D23 sessions — a user disabled or removed
in the directory has their sessions killed at the next sync.

**Trades away.** A staleness window between syncs, worst for *removal* from a group. The login refresh
narrows it for new logins but not for an already-active session.

_Spec p.2_

### D10 — One policy snapshot per operation; extensions re-evaluate against current policy

**Decision.** The resolved decision — groups, limits, priority ceiling, approved duration — is
snapshotted once at submission. An extension is a fresh request, checked against policy and group
membership as they stand at that moment, and the resulting decision is recorded as an
`operation_event` (p.9), not as a revised snapshot.

**Why.** p.2 says "user, group and policy snapshot" in the singular. An earlier draft proposed JSONB
per `operation_revision`; that was over-engineering — for a training job that submits and finishes in
hours the snapshot never differs. Re-evaluating extensions means a tightened limit takes effect
immediately, and a user who left a team cannot keep extending on its allowance.

**Trades away.** An extension can be denied that would have been granted a day earlier, so the denial
must say *why*.

_Spec p.2, p.9_

### D11 — `approved_duration` is bound by policy, and a human can approve beyond it

**Decision.** `approved_duration` defaults to the policy limit for the group and workload type.
Requests within policy need no human. A human approver may grant **more** than the policy limit; that
grant is an explicit audited exception recorded as an `operation_event`, carrying who approved it and
what limit it exceeded.

**Why.** Policy limits should bind by default, or they are not limits. But a hard ceiling with no
override forces every genuine exception into a policy edit, which then applies to the whole group. An
override *recorded as an exception* keeps the limit meaningful and the exception visible.

**Consequences.** An approval exceeding policy must never be folded silently into `approved_duration`
as though policy had allowed it — the exception record is the point.

**Trades away.** An approval path and its UI, and a class of decision a human must now justify.

_Spec p.12_

### D25 — Policy is authored as tables in PostgreSQL, edited through an admin UI

**Decision.** Per-group quotas, maximum durations, priority ceilings and allowed GPU flavors are rows
in PostgreSQL, maintained by platform admins through E.C.H.O's own UI.

**Why.** p.2 assigns policy to PostgreSQL. Keeping it as data means the D10 snapshot reads it locally,
changes are audited by the same event machinery, and no external policy engine sits on the admission
path. An OPA/Rego layer would be more expressive but would move a PostgreSQL-owned concern outside
PostgreSQL, weakening invariant #1.

**Trades away.** Genuinely conditional policy is awkward to express as rows, and there is no
policy-as-code review flow — changes are UI actions, so the audit trail has to carry the review.

_Spec p.2_

### D23 — Sessions: opaque, server-side, immediately revocable

**Decision.** The session is a row in PostgreSQL, not a self-contained token.

**Why.** With D7 there is no identity provider to enforce logout, so E.C.H.O owns revocation. An
opaque server-side session can be killed instantly, and D9's sync can revoke the sessions of anyone
disabled in the directory. A stateless JWT cannot — a dismissed user would keep access until expiry,
which is hard to justify for a platform gating expensive shared hardware.

**Trades away.** A database lookup per request, trivial next to the work being authorized.

## Clusters, namespaces and placement

### D8 — Multi-cluster from day one, with cluster-agnostic workers

**Decision.** E.C.H.O is a multi-cluster control plane. Workers are cluster-agnostic: any worker can
materialize an immutable plan in the plan's selected cluster.

**Why.** p.2 makes "target cluster and resource plan" a PostgreSQL-owned concern and the p.8 execution
plan carries a target cluster, so the concept is first-class in the specification.

**Consequences.** A cluster registry, per-cluster credentials (D19), and the namespace layout (D12)
replicated per cluster. Because workers are cluster-agnostic rather than per-cluster, there is one
Deployment per identity holding N cluster clients — and since controller-runtime's cache is
per-cluster, that means N managers in one process. Memory scales with cluster count, one process holds
fleet-wide credentials, and an unhealthy cluster's reconnect storm can disturb the others; per-manager
isolation is needed to contain that.

**Trades away.** Meaningfully more work before the first Training slice runs.

_Spec p.2, p.8_

### D24 — Authority after `EchoExecution` is created

**Decision.** Once `EchoExecution` exists, **Kubernetes is authoritative for runtime state.**
PostgreSQL holds the original submission, the immutable execution plan, queue records, audit history,
usage accounting and *projections* of Kubernetes state. Its `Dispatching`, `Running` and terminal
records are projections, not independent runtime truth.

**Why.** This is p.2's ownership table read from the other side — "actual workload state → Kubernetes
API", and "PostgreSQL can report runtime state, but that state is a projection of Kubernetes, not an
independent truth." Stating it explicitly prevents the common error of treating a stale PostgreSQL
`Running` row as fact.

**Not a deviation.** Invariant #1 still holds: PostgreSQL is the source of truth for *desired state and
intent*, which is what "source of truth" means in the specification.

_Spec p.2, p.5_

### D12 — Namespace architecture: `echo-<project>-<type>`, two workload identities

**Decision.** Per cluster:

```text
echo-access
├── echo-services-controller     # bound to every *-inference namespace
├── echo-compute-controller      # bound to every *-training and *-research namespace
└── echo-project-controller      # see D14

echo-vision-inference
echo-vision-training
echo-vision-research
echo-nlp-inference
echo-nlp-training
echo-nlp-research
```

Selection is by label, never by parsing namespace names:

```yaml
metadata:
  labels:
    echo.erez.io/project: vision
    echo.erez.io/resource-type: research
    echo.erez.io/managed: "true"
```

**Why the two identities.** They move the isolation boundary from the cluster layer to the
authorization layer: a bug or compromise in the compute reconciler cannot delete an inference
Deployment, because `echo-compute-controller` holds no verbs in `*-inference`. That protects live
inference without fragmenting the fleet, so all capacity stays in one Kueue cohort and borrowing works.
It also falls out as clean least privilege — the services identity needs Deployment/Service/HPA verbs,
the compute identity needs Job/StatefulSet/PVC verbs, and neither needs the other's.

**Why per-project namespaces.** They are the unit that carries GPU/CPU/memory quota, Secrets and PVCs,
NetworkPolicy, Kubernetes RBAC, Kueue LocalQueues, workload visibility and logs, and deletion blast
radius. p.5's `namespace: echo-project-vision` points the same way.

**Access policy.** E.C.H.O is the normal interface. Operators have cluster access. Project users get
namespace-scoped access only where Research genuinely requires it — never cluster-wide.

**Label domain.** `echo.erez.io/` throughout, matching the spec's `echo.erez.io/operation-id` and
`operations.echo.erez.io/v1alpha1` (p.5). One domain, one selector prefix.

**Consequences.** A pod carries exactly one ServiceAccount, so the two workload identities mean a
services worker and a compute worker as **separate Deployments** — which is wanted anyway, since
inference reconciliation is low-volume and steady while training is bursty. `LocalQueue` is
namespace-scoped, so LocalQueues are per project-type namespace and team fair-sharing lives in
ClusterQueues and cohorts (D15).

**Namespaces are an authorization boundary, not a resource one.** They do not stop a training pod
landing on an inference node and starving it on CPU, memory or PCIe bandwidth, and they give no
control-plane isolation — an etcd or API-server problem hits everything. Hence D13.

**Trades away.** Many namespaces, and a RoleBinding set per project that must be provisioned (D14).

_Spec p.5_

### D13 — Dedicated inference node pool, tainted

**Decision.** Inference nodes are tainted; only inference workloads tolerate them. Paired with a
non-preemptible priority class for inference.

**Why.** D12's namespaces isolate authorization, not resources. Hard node separation is what actually
protects the workload serving live traffic.

**Trades away.** Utilization — idle inference nodes cannot take training work.

### D14 — Project provisioning reconciles a `project` row, via `echo-project-controller`

**Decision.** A project is desired state in PostgreSQL. A worker reconciles it into the namespace set,
RoleBindings, ResourceQuota, NetworkPolicy and LocalQueue from a template. A third identity,
`echo-project-controller`, holds cluster-scoped rights over namespaces, RoleBindings, quotas,
NetworkPolicies and LocalQueues — and **no** rights over Pods, Jobs or Deployments.

**Why.** Provisioning is a Kubernetes write, so doing it from the API would break D1's boundary and
require Kubernetes client code in Python. Reconciling from a row keeps the boundary, is idempotent on
retry, and gets Server-Side Apply field ownership for free (invariant #11) — so a hand-edited quota is
detected or overwritten. Splitting the identity preserves least privilege: the thing that can create
namespaces cannot run workloads, and the things that run workloads cannot mint their own permissions.

**Trades away.** Project creation is eventually consistent rather than synchronous, so the API returns
before the namespaces exist.

### D15 — Two ClusterQueues per team, in two cohorts

**Decision.** `<team>-compute` covers that team's training and research LocalQueues; `<team>-inference`
covers its inference LocalQueue. A compute cohort allows borrowing across teams on the shared
training/research pool; a separate inference cohort sits on the D13 tainted pool.

**Why.** Keeps p.5's team-level `queue: team-vision` idea while respecting the node split — one queue
spanning both pools would let admission draw on quota that lives on nodes the workload cannot use.
Borrowing with preemption in the compute cohort is what serves p.1's "maximize GPU utilization".

**Trades away.** Preempted compute workloads must tolerate being killed, which leans on D17.

_Spec p.5, p.11_

### D18 — Cluster selection is an explicit user choice; the planner validates

**Decision.** The submitter names the target cluster. The planner validates that choice against policy
and flavor availability and writes it into the immutable plan.

> **Deviation.** p.8's execution-plan note says "the plan ranks placement". With explicit choice there
> is no ranking — the planner validates rather than selects.

**Why.** Simplest and fully predictable, and it keeps E.C.H.O far away from anything resembling
capacity arbitration.

**Consequences.** It resolves checkpoint portability by construction: since the cluster is part of
submitted intent, a retry re-planned through `Queued → Planning` validates the *same* cluster and
cannot wander to one lacking its PVC. It requires the D2 UI to show per-cluster capacity, or the choice
is a guess. And there is no automatic failover — if the chosen cluster is saturated or unhealthy the
operation waits rather than relocating.

**Trades away.** Hot and cold clusters, since nothing balances load across them.

_Spec p.8, p.11_

### D19 — Per-cluster ServiceAccount tokens, held as Secrets

**Decision.** Each cluster defines the D12/D14 ServiceAccounts in `echo-access`; their tokens are
stored as Secrets where the cluster-agnostic workers run.

**Why.** Simple and native, and RBAC stays enforced by each target cluster rather than by E.C.H.O.

**Trades away.** One namespace holds fleet-wide credentials, and rotation is per-cluster.

### D20 — Inference placement: the model is the scheduling unit

**Decision.**

- The scheduling unit for inference is the **model**, not the Pod.
- A model is assigned to a **single node** and may use one or more **whole GPUs** on that node. No MIG,
  no time-slicing.
- The planner selects the target cluster but **never a node, and never calculates free GPU capacity**.
  Kueue admits on quota; the Kubernetes scheduler and device plugin atomically select a compatible node
  and allocate the GPUs. The worker **observes** the result rather than allocating devices.
- Once placed, the assigned node is **fixed for the operation's lifetime**. Kubernetes may restart or
  recreate the model's Pods, but they must return to that node. The binding is held in
  `EchoOperation.status` — see **D26**, which introduces the operation-scoped parent that makes this
  possible; the attempt-scoped `EchoExecution` cannot own it.
- If placement does not succeed within a configured deadline, the operation fails with
  `reason=PlacementFailed`. **Derived:** this is a reason on `Failed`, not a sixth terminal state,
  since invariant #7 fixes the terminal set.
- E.C.H.O does **not** provide zero-downtime inference, redundant replicas or active-active deployment.
  Each model has exactly one active placement. A per-operation recovery policy decides only whether that
  placement is held after node loss (`HoldPlacement`) or may move to another compatible node
  (`Relocate`); relocation may cause downtime while the model is allocated and loaded again. See D26.
- Multi-node models are **out of scope** and managed directly.

**Why this preserves invariant #8.** Observe-then-record means E.C.H.O never counts free GPUs and never
claims a reservation. "Pods return to the same node" is satisfied by remembering a node the scheduler
chose, not by choosing one. p.11's layer table stays intact.

**Trades away.** Single placement means a model is unavailable during node loss or relocation.
Workloads needing redundancy are outside E.C.H.O.

_Spec p.11, invariant #8_

## Lifecycle, retries and observation

### D16 — `Checkpointing` is continuous orchestration-milestone recording, not a lifecycle phase

**Decision.** "Checkpointing" means persisting the **operation's orchestration progress** — validation,
planning, workload creation, admission, startup — so that if a worker or runtime Job fails,
reconciliation continues from the last confirmed milestone instead of restarting the operation. The
checkpoint is bound to the **operation**, not to a Pod: Kubernetes may replace Pods without restarting
the E.C.H.O operation. It does **not** restore application memory or computational progress inside the
container.

Milestones live in **PostgreSQL**, projected into `EchoExecution.status` for `kubectl` visibility
(p.6's "easier inspection").

> **Deviation.** The p.7 diagram draws `Checkpointing` as a discrete state between `Running` and
> `Retrying`. It is not a phase — milestone recording happens continuously as operation status is
> reconciled, so the state is removed.

**Why PostgreSQL and not the CR.** p.5 is explicit that the CR "is a derived execution snapshot, not
the system of record." If the reconciler *resumed from* milestones held in the CR, the CR would become
the system of record for orchestration progress. The CR is also attempt-scoped (`echo-7d91a2-a1`, with
an `echo.erez.io/attempt` label), so attempt 2's CR starts empty — structurally the wrong container for
operation-scoped milestones.

**Safety property.** Record milestones freely, but let **observed state drive the next action**. A
reconciler that branches on "milestone says workload created, so skip creation" is edge-triggered and
will be wrong the moment something is deleted after the milestone was written. p.13's "next worker
finds deterministic object" resumes by re-deriving from reality; Server-Side Apply makes re-applying
cheap and idempotent. Milestones are for observability and audit, not control flow.

**Note on how little is left to save.** p.8 already stores the immutable execution plan in PostgreSQL,
and `processed_generation` (p.10) is already an orchestration-progress marker. Validation and planning
results are persisted, workload creation is an idempotent apply, admission belongs to Kueue and cannot
be skipped, and startup is observed. The milestone list also maps nearly one-to-one onto the p.7
lifecycle states, which D6 already records via the transition function and `operation_event`.

**Trades away.** Two things, honestly. Making it continuous rather than a phase means there is no
lifecycle state showing "this operation is saving progress" — that information lives only in milestone
and event rows, so an operator reads it from a detail view rather than the lifecycle. And because
milestones are advisory rather than control flow, they do not actually save re-doing work on resume; the
reconciler re-derives from observed state either way. What this buys is observability and audit, not
speed. The original goal of "avoid repeating completed control-plane steps" is largely already met by
the immutable plan and `processed_generation`, as noted above.

_Spec p.5, p.6, p.7, p.8, p.10, p.13_

### D17 — Retry classification

**Decision.** Retry automatically:

- Node loss
- Infrastructure eviction or preemption
- Spot interruption
- Transient registry or network failure
- Cluster capacity disappearing after placement

Do **not** retry automatically:

- Invalid image, or image not found
- Image authorization failure
- Invalid command or specification
- Application non-zero exit
- RBAC or policy rejection
- Deterministic OOM from insufficient requested memory

Unknown causes: **do not retry by default.** Preserve the reason and allow an explicit user or
operator retry.

**Why.** p.5's own examples split this way — "node disappeared" should retry, "image was invalid"
cannot succeed on retry.

**Consequences.** The manual retry path is an API surface the specification does not have, and it must
still go through the single-transaction attempt increment — invariant #5 admits no exception for a
human-triggered retry. Deterministic OOM is hard to separate from co-tenancy OOM, since D13 tainted
only the inference pool and training shares nodes with research; classifying it confidently needs the
request/limit comparison, not just `OOMKilled`.

_Spec p.5, p.7, p.13_

## Operations

### D21 — Usage accounting: aggregated buckets, allocated and observed kept separate

**Decision.** Raw GPU telemetry stays in Prometheus. E.C.H.O periodically aggregates it into durable
PostgreSQL usage buckets.

- Training and Research usage is recorded **per operation attempt** and finalized when the attempt
  terminates.
- Inference usage is recorded **continuously per model operation**, because inference has no natural
  terminal state.
- PostgreSQL stores **allocated GPU time** and **observed utilization** separately, and does not
  duplicate every raw Prometheus sample.

**Why.** Matches p.2's "summarized into PostgreSQL". Keeping allocated and observed apart is what
exposes a job holding eight GPUs at 4% — chargeback bills allocation, efficiency review reads
utilization, and collapsing them hides the waste the platform exists to prevent.

**Consequence.** Inference usage accrues indefinitely with no terminal event to close it, so D22's
partitioning applies to the usage tables too, not only to events.

_Spec p.2, p.8_

### D22 — Retention: monthly partitions, retained indefinitely, cold partitions archived

**Decision.** `operation_event` and `compute_operation` history are partitioned by month and retained
indefinitely; cold partitions move to cheaper storage or are detached and archived.

**Why.** p.1 makes audit history a first-class concern, so nothing is deleted. Monthly partitions keep
the hot set fast.

**Trades away.** Partition maintenance becomes a scheduled job that must not be allowed to fail
silently.

_Spec p.1_

### D26 — `EchoOperation`, an operation-scoped parent resource

**Decision.** The attempt-scoped `EchoExecution` cannot own a model's persistent node binding, so an
operation-scoped parent is introduced:

```text
EchoOperation: echo-7d91a2
├── EchoExecution: echo-7d91a2-a1
├── EchoExecution: echo-7d91a2-a2
└── ...
```

PostgreSQL remains authoritative for the *requested* recovery policy; `EchoOperation.status` is
authoritative for the *observed* placement and availability. Attempt CRs inherit the parent's placement,
so the pin survives attempt replacement.

| Location | Responsibility |
| --- | --- |
| PostgreSQL desired state | User intent, recovery policy and desired generation |
| `EchoOperation.status` | Persistent operation placement, availability and current attempt |
| `EchoExecution.status` | Attempt-specific workload state |
| Pod | Ephemeral runtime instance |

```yaml
status:
  phase: Active
  placement:
    generation: 1
    cluster: cluster-a
    nodeName: gpu-node-07
    nodeUID: ...
  currentExecution:
    name: echo-7d91a2-a2
  conditions:
    - type: Ready
      status: "False"
      reason: PinnedNodeUnavailable
    - type: Degraded
      status: "True"
      reason: PinnedNodeUnavailable
```

**Why PostgreSQL is *not* authoritative here.** Kubernetes is authoritative for runtime placement (p.2:
"actual workload state → Kubernetes API"), and an observed node binding is runtime state. This is the
mirror image of D16: orchestration milestones are control-plane facts, so PostgreSQL owns them; an
observed binding is a cluster fact, so the cluster owns it. Consistent with D24. PostgreSQL still
*projects* placement for reporting and the D2 capacity views — a projection, never the source.

**Node-loss behaviour.** `Failed` is not used for a temporary node outage.

```text
HoldPlacement:  Running → Unavailable → Starting → Running
                              │
                              └── wait for the pinned node

Relocate:       Running → Unavailable → Relocating → Starting → Running
```

Under `HoldPlacement` the operation stays non-terminal and waits; when the node returns, E.C.H.O
recreates the Pod on the same node. Under `Relocate`, relocation increments `placement.generation` and
permits a new node assignment.

> **Deviation.** `Unavailable` and `Relocating` are non-terminal states the p.7 lifecycle diagram does
> not contain. They violate no invariant — invariant #7 constrains only the terminal set — but the
> diagram is now incomplete.

**Refines invariant #15.** With `HoldPlacement`, the node binding is fixed for the operation's lifetime
unless explicitly changed by the user. With `Relocate`, the binding is fixed *within a placement
generation*, and relocation creates a new generation.

**Consequences.**

- `placement.generation` is a **fourth counter**, distinct from `desired_generation`, `claim_epoch` and
  `attempt_number` — see the counter table in `03-data-and-worker-protocol.md`. Many attempts live under
  one placement generation; that asymmetry is the whole point.
- `EchoOperation` owns the `EchoExecution` objects, which own the Jobs, Services and PVCs, so p.6's
  owner-reference garbage collection now cascades from the operation. The workload identities (D12) need
  create rights on `EchoOperation` in their namespaces.
- **Derived:** `Unavailable` applies to operations with a *persistent* placement — inference. Node loss
  for training and research follows D17's retry path (`Running → Retrying → Queued`), since those have
  no pin to wait for. Listed in `99-open-questions.md` for confirmation.

**Trades away.** A second CRD, its schema and its controller, plus a parent/child status reconciliation
that can disagree with itself. Under `HoldPlacement` an operation can wait indefinitely on a node that
never returns, so the condition needs alerting or it becomes a silent outage.

_Spec p.2, p.5, p.6, p.7_

## Workload models

### D27 — Two workload models: bring-your-own image, and managed inference

**Decision.** E.C.H.O supports two workload models.

**Research and Training — bring-your-own image.** The user supplies a complete container image
containing the required tools and code. E.C.H.O treats the image as **opaque**: it validates the
*request*, allocates the requested compute, and runs it as a time-bound session or job. It does not
inspect or construct the image.

**Inference — a managed service.** E.C.H.O owns and maintains a **versioned, prebuilt inference runtime
image** containing vLLM and the required platform components. The client supplies a **declarative
inference specification**:

- model-weight references
- optional adapters or layers
- validated vLLM configuration
- GPU requirements

E.C.H.O validates that specification and materializes the complete Kubernetes stack — model Pod,
configuration, model storage access, Service and external route. Once the model is ready the client
receives a **stable inference endpoint**, not access to the underlying Pod.

End users do not receive Kubernetes or Pod access for managed inference. Platform administrators retain
direct access to the underlying resources through Kubernetes RBAC for logging, diagnostics and
maintenance.

**Why.** The two models want opposite things. Research and Training are open-ended — the user's code is
the point, so anything E.C.H.O imposed on the image would be in the way. Inference is a product surface
with a stable contract, where owning the runtime is what makes validation, upgrades and a durable
endpoint possible at all. It also aligns the access boundary with D12: end users have no reason to reach
into a managed service, and the namespace-scoped access D12 grants for Research is exactly where
interactive access does belong.

**Consequences.**

- **`workload` in the CR is type-dependent.** p.5's example carries `image` and `command`, which is the
  user's image for Training. For Inference, `image` is *E.C.H.O's* runtime image and `command` is not
  user-supplied at all — the model specification replaces it. A reader of p.5 would otherwise assume
  `workload.image` is always the submitter's.
- **The Service and external route belong to `EchoOperation`, not `EchoExecution`.** This follows from
  D26: `EchoExecution` is attempt-scoped, so an attempt replacement would delete and recreate anything it
  owns. A "stable inference endpoint" cannot be owned by the attempt. Endpoint, Service and route sit on
  the operation-scoped parent; only the Pod is attempt-scoped.
- **E.C.H.O now owns a build artifact.** The inference runtime image needs a build pipeline, a registry, a
  version catalog, and an upgrade story for already-running models — which connects to p.11's
  "deployment update strategy" for inference.
- **The inference validator is version-coupled to the runtime.** Validating vLLM configuration means
  knowing the valid option surface *of the version shipped*, so every runtime upgrade can change what
  validates. This is the main ongoing cost of the managed model.
- **`echo-services-controller` needs a wider verb set** than D12 assumed: on top of Deployment, Service
  and HPA, it now needs ConfigMaps for the vLLM configuration, model-storage access (a PVC or an
  object-store credential Secret), and the external route — Ingress or Gateway API `HTTPRoute`.
- **Model weights load at runtime, not at build.** Cold start is therefore a function of weight size and
  storage throughput, which is exactly the downtime D26's `Relocate` path already acknowledges ("allocated
  and loaded again"). The D14 namespace template must provision the project's model-storage access.
- **Research and Training run arbitrary user code on shared nodes.** D13 tainted only the inference pool,
  so training and research are co-tenant with other projects' arbitrary images. The D14 namespace template
  is where that is contained — PodSecurity admission level, no privileged containers, no `hostPath`,
  seccomp — and no such level has been chosen yet.

**Trades away.** Inference flexibility: a client that wants a serving stack other than the shipped vLLM
runtime cannot have one through E.C.H.O. And E.C.H.O takes on permanent maintenance of a runtime image
and a configuration validator that must track it.

_Spec p.5, p.11_

## Defaults taken without a round trip

Settled by the specification or by a prior decision. Recorded so they are visible and can be vetoed,
not because they were deliberated.

| # | Default | Basis |
| --- | --- | --- |
| V1 | Idempotency conflict: `INSERT ... ON CONFLICT (owner_subject, idempotency_key) DO NOTHING RETURNING`, then select and return the original when no row comes back | p.9 requires exactly this behaviour |
| V2 | Lease duration: 30 seconds | The p.10 example. Must exceed the worst-case reconcile pause; wants load-testing |
| V3 | Backoff: exponential with jitter and a cap, expressed by pushing `available_at` forward | p.13 "backoff and keep operation pending"; controller-runtime's rate-limited requeue covers the Kubernetes side |
| V4 | Training materializes as `batch/v1` Job under Kueue; JobSet when multi-node training arrives | p.11, and Training is the first slice |
| V5 | Platform limits are checked in the planner/policy engine before Kueue sees the workload; Kueue enforces only cluster quota | p.11 layer table — the planner "validates policy"; Kueue must not "apply organizational business policies" |
| V6 | `ResourceFlavor` names follow model-and-capacity form, e.g. `a100-80gb` | The p.5 CR example |

**Superseded defaults.** An earlier default set `owner_subject` to the OIDC `sub` claim — replaced by
D7. An earlier default placed one worker Deployment per cluster — replaced by D8's cluster-agnostic
workers and D12's per-identity Deployments.
