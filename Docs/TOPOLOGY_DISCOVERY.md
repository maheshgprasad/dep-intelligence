# Topology discovery

`repos.txt` is the repository allowlist. It does not declare which services call each other.

Discovery reads deterministic sources from those repositories: package names, OpenAPI operations, Kubernetes Service names, Compose service names, HTTP routes, and absolute client URLs. A relative URL such as `POST /api/auth/validate` stays unresolved. Sharing a route path with another service is not enough to name a provider.

Optional `nami-topology.yaml` resolves an ambiguous host:

```yaml
aliases:
  payment.internal:
    service: payment-service
```

`cluster-manifest.json` remains a compatibility input for client bindings and host aliases. It is not required when a host already matches an analyzed service.

Artifacts:

```text
output/topology/discovered.json
output/topology/effective.json
```

Discovery reads an existing local checkout. It does not clone a repository or call GitHub. An allowlist URL without a checkout is listed as a service and marked not scanned.

Refresh intelligence writes the artifacts. `python scripts/nami-topology.py discover` writes them without a dashboard. `GET /api/topology` reads the stored effective file and does not scan again.
