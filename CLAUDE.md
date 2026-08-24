# E.C.H.O — project context

**E.C.H.O (Erez Compute Host Operations)** is a web-based GPU orchestration platform built on top of
Kubernetes. It provides a unified interface for submitting, scheduling, monitoring, and managing AI
compute operations across three workload types: long-running inference services, time-bound research
environments, and time-bound training jobs. PostgreSQL is the central source of truth for requests,
queues, policies, metadata, and audit history; E.C.H.O workers reconcile that desired state with
Kubernetes, while Kueue manages GPU quota admission and the Kubernetes scheduler places workloads
onto GPU nodes. Organizational users and groups enforce access, priorities, limits, and fair sharing.

The repository is pre-implementation. Behaviour comes from two places: the specification,
`docs/specification/E.C.H.O.pdf`, transcribed into `docs/context/01`–`05`; and the decisions that answer
what the specification leaves open, in `docs/context/06-decisions.md`. Where the two disagree, the
decisions win and say so — see **Deviations** below.

## Stack

Decided 2026-08-24; rationale and trade-offs in `docs/context/06-decisions.md`.

| Layer | Choice |
| --- | --- |
| Control plane (API, planner, policy engine, expiry/recovery scanner) | Python + FastAPI, one package with `api` / `planner` / `scanner` entrypoints |
| Worker pool | Go + controller-runtime, cluster-agnostic, one Deployment per identity |
| Web UI | React + TypeScript + Vite, built to static assets and served by the API |
| Database | PostgreSQL. Lifecycle enforced in-database (transition function + trigger). Alembic runs hand-written migrations; SQLAlchemy is the query layer, never the schema's author |
| Clusters | Multi-cluster. Namespaces `echo-<project>-<type>`; identities in `echo-access`; dedicated tainted inference node pool |
| CRDs | Two: `EchoOperation` (operation-scoped, owns placement) parents `EchoExecution` (attempt-scoped, owns workload state) |
| Workload models | Research/Training: user supplies an opaque image. Inference: managed service on E.C.H.O's versioned vLLM runtime — client supplies a declarative spec, receives a stable endpoint |
| Auth | LDAP only — no OIDC. The API binds and issues its own revocable server-side session |

## System ownership

Never move a concern to a different owner without changing this table first.

| Concern | Authority |
| --- | --- |
| User request and desired state | PostgreSQL writer |
| Workflow/reconciliation queue | PostgreSQL writer |
| User, group and policy snapshot | PostgreSQL |
| Target cluster and resource plan | PostgreSQL |
| Logical GPU admission | Kueue |
| Pod-to-node placement | Kubernetes scheduler |
| Actual workload state | Kubernetes API |
| Reporting and dashboards | PostgreSQL replicas |
| GPU telemetry | Prometheus/DCGM, summarized into PostgreSQL |

_Spec p.2_

## Non-negotiable invariants

Each of these is a review rule. Code that breaks one is wrong even if it passes tests.

1. **PostgreSQL is the source of truth.** PostgreSQL can report runtime state, but that state is a
   *projection* of Kubernetes, not an independent truth. The `EchoExecution` CR is a derived
   execution snapshot, never the system of record. _p.2, p.5_
2. **Read replicas are never used for correctness decisions** — reporting and dashboards only. _p.2, p.13_
3. **Never hold a PostgreSQL transaction open while calling Kubernetes.** _p.10_
4. **`LISTEN/NOTIFY` is a wake-up hint, not the durable queue.** It can wake workers quickly, but
   polling remains the reliable fallback. _p.10_
5. **Workers never independently invent a new attempt.** Retry creation happens through one
   PostgreSQL transaction that increments the attempt number. Attempt identity is deterministic:
   `echo-<operation-id>-a<attempt-number>`. _p.5_
6. **Every completion update carries the fencing token** (`lease_owner` + `claim_epoch`). Zero
   updated rows means the worker lost its lease and **must discard its result**. _p.10_
7. **Terminal states never move backward:** `Succeeded`, `Failed`, `Cancelled`, `Expired`,
   `Rejected`. **Enforced in PostgreSQL** — a transition function is the write path and a trigger is the
   backstop, so an ad-hoc `UPDATE` cannot violate it either. That is where a reviewer verifies it, not in
   application code. _p.7, D6_
8. **E.C.H.O never counts "currently free GPUs" and never claims a physical GPU is reserved.** That
   races Kubernetes. The planner validates the requested cluster (D18); Kueue is the final authority on
   quota admission; the Kubernetes scheduler and device plugin bind the node and allocate the GPUs.
   _p.11, p.8 diagram, D18_
9. **Time-bound clocks start at admission, not submission:**
   `expires_at = admitted_at + approved_duration`. Expiration and extension must use an expected
   database version so a stale expiry scanner cannot override a newly approved extension. _p.12_
10. **Cancellation changes durable intent.** A delayed `START` command cannot revive a workload,
    because commands are only wake-up hints — the worker always reads the newest desired state. _p.12_
11. **Kubernetes writes use Server-Side Apply** with explicit field ownership, so manual workload
    modification is detected or overwritten on owned fields. _p.13_
12. **Submission is one transaction with a client idempotency key.** `UNIQUE (owner_subject,
    idempotency_key)`; a retried HTTP request returns the original operation instead of creating a
    duplicate. If the transaction fails, nothing was submitted. `owner_subject` must be an **immutable**
    directory identifier (`objectGUID` / `entryUUID`) — never a DN or username, which can be renamed and
    would silently break idempotency for that user. _p.9, D7_
13. **An approval that exceeds a policy limit is recorded as an explicit exception.** It is never folded
    into `approved_duration` as though policy had allowed it. The exception record — who approved, what
    limit was exceeded — is the point. _D11_
14. **Orchestration milestones are recorded, but observed state drives the next action.** A reconciler
    that branches on "milestone says done, so skip" is edge-triggered and wrong the moment something is
    deleted behind it. Milestones are for audit and observability. _p.13, D16_
15. **E.C.H.O never selects a node and never computes free GPU capacity.** For pinned inference it
    *observes* the node the scheduler chose and remembers it in `EchoOperation.status`. Remembering a
    placement is allowed; choosing one is not. Under `HoldPlacement` the binding is fixed for the
    operation's lifetime unless the user changes it; under `Relocate` it is fixed *within a placement
    generation*, and relocating creates a new generation. _p.11, D20, D26_
16. **End users never receive Kubernetes or Pod access for managed inference.** They get a stable
    endpoint. Platform administrators keep direct access through Kubernetes RBAC for logging, diagnostics
    and maintenance. Conversely, E.C.H.O never inspects or constructs a user-supplied Research or
    Training image — it validates the request, not the contents. _D27_

## Deviations from the specification

Four decisions depart from specification text or diagrams. Each is recorded in
`docs/context/06-decisions.md`; do not "fix" the code to match the PDF on these points.

| Deviation | Spec says | We do |
| --- | --- | --- |
| **D7** | p.8: "OIDC identity verified", user is an "OIDC subject" | LDAP only; the API issues its own session |
| **D16** | p.7 draws `Checkpointing` as a state between `Running` and `Retrying` | Not a phase — milestone recording is continuous |
| **D18** | p.8: "the plan ranks placement" | The submitter chooses the cluster; the planner validates |
| **D26** | p.7 has no `Unavailable` or `Relocating` state | Both added as non-terminal states, so a pinned-node outage is not `Failed` |

## Build order

Training is the first vertical slice: it has a clear beginning and a terminal result. _p.11_

## Where things are written down

| File | Contents |
| --- | --- |
| `docs/context/01-overview.md` | Purpose, workload types, ownership, high-level architecture |
| `docs/context/02-domain-model.md` | Worker roles, execution attempts, `EchoExecution` CR, job lifecycle |
| `docs/context/03-data-and-worker-protocol.md` | Submission transaction, claiming SQL, fencing, end-to-end sequence |
| `docs/context/04-scheduling-and-resource-types.md` | Three scheduling layers, per-type materialization |
| `docs/context/05-lifecycle-and-failures.md` | Cancellation, expiration, failure-recovery matrix |
| `docs/context/06-decisions.md` | **D1–D27** — every answered question, with rationale and trade-offs |
| `docs/context/07-implementation-plan.md` | Build order as small reviewable PRs, with per-step review checklists |
| `docs/context/99-open-questions.md` | What remains open, including questions the decisions created |
| `docs/specification/E.C.H.O-Platform-Specification.pdf` | **The specification** (48 pages) — RFC-style, 82 numbered requirements, 10 diagrams; standalone and supersedes the original |
| `docs/specification/src/` | Sources for that PDF; `./make.sh` rebuilds it |
| `docs/specification/E.C.H.O.pdf` | The original brief (13 pages), kept for provenance |

## Conventions in these documents

- `_Spec p.N_` marks the source page, so every claim is traceable.
- SQL and YAML are reproduced verbatim from the specification. Do not "improve" them.
- **Derived:** prefixes anything that is inference rather than specification text.
