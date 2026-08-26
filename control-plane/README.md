# E.C.H.O control plane

One Python package exposes three separate process entrypoints:

- `echo-api`
- `echo-planner`
- `echo-scanner`

The package will own database migrations in a later task. Kubernetes clients do not belong in this
project.

Each entrypoint currently prints its identity and exits successfully. FastAPI is installed for
future API work, but this scaffold intentionally exposes no server or HTTP routes.
