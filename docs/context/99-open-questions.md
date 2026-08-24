# Open questions

The twenty questions this file originally held — everything the specification declines to decide — were
answered on 2026-08-24 and are recorded in [`06-decisions.md`](06-decisions.md) as D1–D27, plus the
vetoable defaults V1–V6.

What remains below are questions the *decisions themselves* created. They are recorded here rather than
buried inside a decision entry, so the honest state of the design stays visible.

## Gaps in the lifecycle

- **What is the default recovery policy — `HoldPlacement` or `Relocate`?** D26 defines both and D20 makes
  it per-operation, but neither says which applies when the submitter expresses no preference. The two
  differ in whether an outage means waiting or downtime-plus-relocation, so the default is a real
  product choice.
- **Confirm that `Unavailable` is inference-only.** D26 records as **Derived** that training and research
  node loss follows D17's retry path (`Running → Retrying → Queued`) rather than entering `Unavailable`,
  since they have no pin to wait for. Reasonable, but not stated by anyone yet.

## Values that decisions left unset

Each of these has a decided *mechanism* and an undecided *number*. None can be picked well without
load-testing or an operational target.

- Directory sync interval (D9) — sets the worst-case window for a group removal taking effect.
- Session lifetime (D23) — the mechanism is revocable server-side sessions; the duration is open.
- `max_attempts` per workload type (D17).
- Placement deadline before `PlacementFailed` (D20) — must exceed realistic queue wait under Kueue.
- Usage aggregation interval (D21).
- Milestone projection frequency into `EchoExecution.status` (D16) — too eager and it hot-loops the
  Kubernetes API server for no observability gain.
- Lease duration (V2) — the specification's 30 seconds is adopted, but it must exceed the worst-case
  reconcile pause, which is not yet measured.

## Kueue configuration detail

- **Borrowing and preemption limits within the compute cohort** (D15). Borrowing is decided; how much a
  team may borrow, and what preemption policy reclaims it, is not. This interacts directly with D17 —
  aggressive preemption is only safe for workloads that tolerate being killed.
- **Whether research runs at a distinct preemptible priority class below `standard-training`**, as p.11's
  "normally lower/preemptible priority" implies. The class name and its relative value are unset.

## The managed inference runtime (D27)

- **What happens to a running model when a new runtime version ships?** Is the model pinned to the
  runtime version it started on, or rolled forward? p.11 mentions a "deployment update strategy" for
  inference, and D20's single placement with no zero-downtime means any roll-forward is an outage for
  that model. Neither answer is obviously right and nobody has picked one.
- **How is the vLLM configuration validator kept in step with the runtime image?** Validation means
  knowing the valid option surface of the shipped version, so the validator is version-coupled. Whether
  that is generated from the image, hand-maintained per version, or both is undecided.
- **What is the inference endpoint naming and DNS scheme?** "Stable endpoint" needs a concrete form, and
  it must survive attempt replacement and `Relocate` (D26).
- **Where do model weights live, and who provisions access?** D27 says weights are referenced, not baked
  into the image, so the D14 namespace template has to provision the project's model-storage access —
  object-store credentials or a PVC. Which, and how rotated, is open.

## Bring-your-own-image containment (D27)

- **What PodSecurity admission level applies to Research and Training namespaces?** Users supply
  arbitrary images and D13 tainted only the inference pool, so Research and Training are co-tenant with
  other projects' arbitrary code on shared GPU nodes. `baseline` versus `restricted`, and the specifics
  (privileged containers, `hostPath`, seccomp profile), are unchosen — and the D14 namespace template is
  where they would be enforced.
- **Is a submitted image validated to exist before admission?** D17 treats "invalid image or image not
  found" as non-retryable, which implies discovery at runtime rather than at validation. A registry
  pre-check would fail faster but couples admission to registry availability.

## Operational ownership

- **Partition maintenance and archive destination** (D22). Monthly partitions need a scheduled job that
  creates and detaches them; where cold partitions go, and who notices when the job stops running, is
  undecided. A silent failure here is invisible until the hot set is years wide.
- **Per-manager isolation inside the cluster-agnostic worker** (D8). One process holding N
  controller-runtime managers needs a story for one cluster's reconnect storm not starving the others —
  bounded work queues, per-cluster circuit breaking, or something else.
- **Credential rotation procedure** (D19). Per-cluster ServiceAccount tokens live as Secrets; rotation
  is per-cluster and manual unless something is built.
- **Alerting on a long-lived `PinnedNodeUnavailable` condition** (D26). Under `HoldPlacement` an operation
  waits indefinitely for a node that may never return. Without an alert on the condition's age, that is a
  silent outage — the model looks managed and serves nothing.

## Deliberately out of scope

Recorded so they are not mistaken for oversights.

- Zero-downtime inference, redundant replicas, and active-active model deployment (D20). Each model has
  exactly one active placement.
- Multi-node models (D20) — managed directly, outside E.C.H.O.
- Single sign-on (D7) — foreclosed by the LDAP-only constraint.
