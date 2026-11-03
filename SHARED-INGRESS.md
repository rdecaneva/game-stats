# Shared Decan Cloud ingress

Public HTTPS on port 443 is managed by [decan-cloud-nginx](https://github.com/rdecaneva/decan-cloud-nginx). Start that stack and create its external `decan-cloud-services` network before deploying this app. The compose configuration keeps the application's private default network and also joins shared ingress with a unique alias.

Public URL: https://stats.decaneva.cloud. Ingress connects to `stats:8000`; existing application authentication remains enabled. No application rebuild or data migration is needed for proxy routing.

Proxy replicas currently share one physical host. Node HA requires an additional host, traffic failover, and appropriate backend/state availability; see the ingress repo runbook.

Uvicorn trusts forwarded headers from the shared network CIDR (currently `172.25.0.0/16`) so generated asset/login URLs use HTTPS. If deploying on a differently addressed Docker network, set `INGRESS_PROXY_CIDR` to its subnet; inspect with `docker network inspect decan-cloud-services`. Direct LAN requests are not trusted to set forwarded headers.
