# Cancellation, expiration and failure handling

_Source: `docs/specification/E.C.H.O.pdf`, pp. 12–13._

## Cancellation and expiration

Cancellation changes durable intent; a delayed `START` command cannot revive the workload because
commands are only wake-up hints. The worker always reads the newest desired state.

For time-bound operations:

```
expires_at = admitted_at + approved_duration
```

Start the clock at **admission, not submission**. Expiration and extension must use an expected
database version, so an old expiry scanner cannot override a newly approved extension.

_Spec p.12_

## Failure handling

| Failure | Recovery |
| --- | --- |
| API retries submission | Idempotency key returns original operation |
| Two workers claim simultaneously | Row locking and `SKIP LOCKED` |
| Worker pauses past lease | Fencing token rejects stale updates |
| Worker crashes after K8s creation | Next worker finds deterministic object |
| PG transaction fails | Retry complete transaction |
| PostgreSQL failover | Reconnect through stable RW endpoint and reclaim expired leases |
| Kubernetes API unavailable | Backoff and keep operation pending |
| Watch event duplicated | Idempotent upsert and unique event key |
| Watch event missed | Periodic full reconciliation |
| Cancellation races with startup | Latest desired generation eventually removes workload |
| Manual workload modification | Server-Side Apply field ownership detects or overwrites owned fields |
| Read replica lags | Never use replicas for correctness decisions |

Server-Side Apply gives the worker explicit ownership of Kubernetes fields and conflict handling.

_Spec p.13_
