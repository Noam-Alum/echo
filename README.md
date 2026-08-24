# E.C.H.O

**Erez Compute Host Operations** — a web-based GPU orchestration platform on top of Kubernetes. It
gives one interface for submitting, scheduling, monitoring and managing AI compute across three
workload types: long-running inference services, time-bound research environments, and time-bound
training jobs.

PostgreSQL is the source of truth for requests, queues, policies, metadata and audit history.
E.C.H.O workers reconcile that desired state into Kubernetes. Kueue owns GPU quota admission and the
Kubernetes scheduler places pods on nodes — **E.C.H.O never counts free GPUs and never picks a
node.** Organizational users and groups enforce access, priorities, limits and fair sharing.

## Status: pre-implementation

**There is no code in this repository yet.** Not a stub, not a skeleton — nothing. What exists is
the specification, a full transcription of it, every open design question answered, and a build
order.

That is deliberate. The design has a lot of load-bearing detail (fencing tokens, a state machine
enforced in PostgreSQL, four separate counters that are easy to conflate), and it was cheaper to
settle that on paper than to discover it in a rewrite. If you are here to write code, start at
[step 01 of the implementation plan](docs/context/07-implementation-plan.md#01--monorepo-skeleton-and-toolchain).

| What | Where | State |
| --- | --- | --- |
| Specification (13 pages, authoritative) | `docs/specification/E.C.H.O.pdf` | Fixed |
| Transcription, section by section | `docs/context/01`–`05` | Complete |
| Design decisions D1–D27, defaults V1–V6 | `docs/context/06-decisions.md` | Complete |
| Build order as ~39 reviewable PRs | `docs/context/07-implementation-plan.md` | Complete, not started |
| What is still undecided | `docs/context/99-open-questions.md` | Live — read before deciding anything |
| Agent and reviewer context | `CLAUDE.md` | Live |
| Source code | — | Does not exist |

## Read in this order

About an hour, and it is worth doing in order — later documents assume the earlier ones.

1. **`docs/context/01-overview.md`** — purpose, the system ownership table, the architecture
   diagram. The ownership table is the whole design in one page: if you internalize only one thing,
   make it that.
2. **`docs/context/02-domain-model.md`** — execution attempts, the two CRDs, the job lifecycle.
3. **`docs/context/03-data-and-worker-protocol.md`** — the mechanical core. The submission
   transaction, the claiming SQL, fencing, and the **four counters** table. That table is the single
   most misread part of the design; conflating any two of those counters breaks a different
   invariant.
4. **`docs/context/04-scheduling-and-resource-types.md`** — the three scheduling layers and what
   each layer is forbidden to do.
5. **`docs/context/05-lifecycle-and-failures.md`** — cancellation, expiry, and the failure/recovery
   matrix.
6. **`docs/context/06-decisions.md`** — D1–D27. Long, but it is where the answers are. Each entry
   records what was chosen, why, and what it trades away.
7. **`CLAUDE.md`** — the stack, the ownership table, and the **16 non-negotiable invariants**. Those
   invariants are review rules: code that breaks one is wrong even if the tests pass.
8. **`docs/context/07-implementation-plan.md`** — what to build, in what order, and what to look for
   when reviewing each step.

## The decided shape

Full rationale for each of these is in `06-decisions.md`; the summary table is in `CLAUDE.md`.

- **Control plane** — Python + FastAPI, one package with `api` / `planner` / `scanner` entrypoints
  (D1, D4).
- **Workers** — Go + controller-runtime, cluster-agnostic, one Deployment per identity (D1, D8, D12).
- **Web UI** — React + TypeScript + Vite, built to static assets and served by the API (D2).
- **Database** — PostgreSQL. The operation lifecycle is enforced **in the database** — a transition
  function is the write path, a trigger is the backstop. Alembic runs hand-written migrations;
  SQLAlchemy is the query layer and never authors the schema (D5, D6).
- **Auth** — LDAP only, no OIDC. The API binds against the directory and issues its own revocable
  server-side session (D7, D23).
- **CRDs** — two. `EchoOperation` (operation-scoped, owns placement) parents `EchoExecution`
  (attempt-scoped, owns workload state) (D26).
- **Workload models** — two, not three. Research and Training are bring-your-own-image and E.C.H.O
  treats the image as opaque. Inference is a managed service on E.C.H.O's own versioned vLLM runtime,
  where the client sends a declarative spec and receives a stable endpoint — never pod access (D27).

**Training is the first vertical slice**, because it has a clear beginning and a terminal result.
Research and Inference come after.

## How to use these documents

Three rules, and they matter more than they look.

**The decisions win over the PDF.** Where `06-decisions.md` departs from specification text or a
diagram, it is marked **Deviation** and says so. Four of those exist — do not "fix" code to match
the PDF on these points:

| | The PDF says | We do |
| --- | --- | --- |
| **D7** | OIDC identity, user is an "OIDC subject" | LDAP only; the API issues its own session |
| **D16** | `Checkpointing` is a state between `Running` and `Retrying` | Not a phase — milestone recording is continuous |
| **D18** | "the plan ranks placement" | The submitter chooses the cluster; the planner validates |
| **D26** | no `Unavailable` or `Relocating` state | Both added, non-terminal, so a pinned-node outage is not `Failed` |

**Provenance is marked, so trust it.** `_Spec p.N_` cites the source page. **Derived:** prefixes
anything that is our inference rather than specification text. SQL and YAML lifted from the spec are
reproduced verbatim — including things you might want to tidy. Don't; the point is that a migration
can be diffed against the spec character for character.

**Check `99-open-questions.md` before deciding anything.** It holds the questions the decisions
themselves created — the default recovery policy, several unset numbers (lease duration,
`max_attempts`, sync intervals), Kueue borrowing limits, the PodSecurity level for
bring-your-own-image namespaces, and four questions about the managed inference runtime. If your
work touches one of those, you are making a decision, not an implementation choice: record it in
`06-decisions.md` as a new entry and remove it from the open list.

## Contributing, once there is code

The conventions are set out per-step in `07-implementation-plan.md` → Rules for every step. In short:

- **One idea per PR**, ceiling ~400 changed lines including tests.
- **Every PR ships its own tests.** No "tests in a follow-up".
- **A merged migration is immutable.** Fix forward with a new migration — the point of hand-written
  migrations is that the file you reviewed is the file that ran.
- **Name the invariants your PR touches**, using the numbering in `CLAUDE.md`. A PR that touches an
  invariant without naming it is the exact failure mode that list exists to prevent.
- **Test against a real migrated PostgreSQL.** No SQLite, no mocked database. Every rule that
  matters lives in the database, so a fake tests nothing.
- **Never hold a PostgreSQL transaction open across a Kubernetes call.** This is a review question
  on every worker PR, not only the ones that mention it.

`07-implementation-plan.md` also carries an **invariant coverage table** mapping all 16 invariants to
the step that enforces each one and the test that proves it. A blank cell there is a gap in the plan,
not a detail.

## Open questions on the plan itself

The plan's last section lists eight questions for whoever starts step 01. Three of them change the
plan's shape — the repository layout, whether intake validation lives in the API or the planner, and
one step ordering — and are worth answering before the first commit. The other five have stated
assumptions you can proceed on.
