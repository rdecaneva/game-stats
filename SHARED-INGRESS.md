# Shared Decan Cloud ingress

Public HTTPS on port 443 is managed by [decan-cloud-nginx](https://github.com/rdecaneva/decan-cloud-nginx). Start that stack and create its external `decan-cloud-services` network before deploying this app. The compose configuration keeps the application's private default network and also joins shared ingress with a unique alias.

Public URL: https://stats.decaneva.cloud. Ingress connects to `stats:8000`; existing application authentication remains enabled. No application rebuild or data migration is needed for proxy routing.

Proxy replicas currently share one physical host. Node HA requires an additional host, traffic failover, and appropriate backend/state availability; see the ingress repo runbook.
