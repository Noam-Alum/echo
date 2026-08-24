# Scheduling and resource types

_Source: `docs/specification/E.C.H.O.pdf`, p. 11._

## Three scheduling layers

| Layer | Responsibility | Must not do |
| --- | --- | --- |
| E.C.H.O planner | Validate policy, assign priority, choose cluster/queue/GPU flavor | Claim that a physical GPU is reserved |
| Kueue | Atomically reserve quota, fair-share, borrow and preempt | Choose the final node |
| Kubernetes scheduler | Select and bind the physical node | Apply organizational business policies |

This prevents E.C.H.O from racing Kubernetes by trying to count "currently free GPUs."

> **Narrowed by D18.** The submitter chooses the target cluster and the planner *validates* it, so
> the planner does not rank placement. Everything the layer table forbids still holds: E.C.H.O
> never selects a node and never computes free GPU capacity. For pinned inference (D20) it observes
> the node the scheduler chose and remembers it — remembering a placement is allowed, choosing one
> is not.

_Spec p.11_

## Resource-type execution

| Type | Kubernetes materialization | Admission/lifetime |
| --- | --- | --- |
| Training | Job, JobSet, RayJob or Kubeflow training job | Kueue; time-bound; retry/checkpoint policy |
| Research | StatefulSet or interactive Pod, Service and PVC | Time-bound lease; normally lower/preemptible priority |
| Inference | Deployment, Service and autoscaler | No fixed expiry, but bounded replicas and GPU quota |

> **Two workload models, not three (D27).** The middle column reads the same way for all three rows, but
> who *builds* the workload differs. Research and Training are bring-your-own-image: the user supplies a
> complete image and E.C.H.O treats it as opaque, validating the request rather than the contents.
> Inference is a managed service on E.C.H.O's own versioned vLLM runtime image — the client supplies a
> declarative specification and receives a stable endpoint, never Pod access. That also widens what the
> inference materialization includes: ConfigMaps for the vLLM configuration, model-storage access, and an
> external route on top of the Deployment and Service.

_Spec p.11_

### Training first

Training is the best initial vertical slice because it has a clear beginning and terminal result.

### Research

Expiration should stop compute but normally preserve the PVC for a separate retention period:

```
Compute expires → Pod removed → PVC retained 7 days → PVC deleted
```

### Inference

"Unlimited" should mean non-expiring, not unbounded:

```
min replicas
max replicas
maximum GPUs
GPU sharing policy
idle scale-down policy
deployment update strategy
```

_Spec p.11_
