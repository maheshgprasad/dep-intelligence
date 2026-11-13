# Topology discovery

`repos.txt` is the source of truth for repositories, stable service ids, and declared cluster membership. It does not declare which services call each other. `cluster-manifest.json` is generated from `repos.txt`. Topology discovery does not define cluster membership.

Discovery reads deterministic sources from those repositories: package names, OpenAPI operations, Kubernetes Service names, Compose service names, HTTP routes, and absolute client URLs. A relative URL such as `POST /api/auth/validate` stays unresolved. Sharing a route path with another service is not enough to name a provider.

Optional `nami-topology.yaml` resolves an ambiguous host:

```yaml
aliases:
  payment.internal:
    service: payment-service
```

A generated `cluster-manifest.json` lists services and declared clusters. An older hand-written manifest can still supply client bindings and host aliases until it is replaced. It is not required when a host already matches an analyzed service. Discovery writes relationship evidence. It does not move a service between clusters.

Artifacts:

```text
output/topology/discovered.json
output/topology/effective.json
```

Discovery reads an existing local checkout. It does not clone a repository or call GitHub. An allowlist URL without a checkout is listed as a service and marked not scanned.

Refresh intelligence writes the artifacts. `python scripts/nami-topology.py discover` writes them without a dashboard. `GET /api/topology` reads the stored effective file and does not scan again.
