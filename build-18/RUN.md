# Odoo 18 — local test runbook (payment_revolut)

Prereq: **Docker Desktop must be running** (open it from the Start menu; wait until
`docker info` succeeds). The Docker engine here is reachable from the Bash tool context.

All commands run from `C:\Fusion\Revolut_odoo\build-18`. `MSYS_NO_PATHCONV=1` stops Git Bash
mangling the container path in `-v` mounts.

```bash
# 1. Network + Postgres (once)
docker network create rev18net
docker run -d --name rev18-db --network rev18net \
  -e POSTGRES_DB=postgres -e POSTGRES_USER=odoo -e POSTGRES_PASSWORD=odoo postgres:16

# 2. Install smoke test (installs the module, then exits; exit 0 + no traceback = pass)
MSYS_NO_PATHCONV=1 docker run --rm --network rev18net \
  -e HOST=rev18-db -e USER=odoo -e PASSWORD=odoo \
  -v "/c/Fusion/Revolut_odoo/build-18/payment_revolut:/mnt/extra-addons/payment_revolut" \
  odoo:18 odoo -d rev18 -i payment_revolut --stop-after-init

# 3. Run the test suite
MSYS_NO_PATHCONV=1 docker run --rm --network rev18net \
  -e HOST=rev18-db -e USER=odoo -e PASSWORD=odoo \
  -v "/c/Fusion/Revolut_odoo/build-18/payment_revolut:/mnt/extra-addons/payment_revolut" \
  odoo:18 odoo -d rev18 -u payment_revolut \
  --test-enable --test-tags /payment_revolut --stop-after-init

# 4. Live UI on http://localhost:8069 (login admin/admin) for a manual €1 smoke test
MSYS_NO_PATHCONV=1 docker run -d --name rev18-odoo --network rev18net -p 8069:8069 \
  -e HOST=rev18-db -e USER=odoo -e PASSWORD=odoo \
  -v "/c/Fusion/Revolut_odoo/build-18/payment_revolut:/mnt/extra-addons/payment_revolut" \
  odoo:18

# Teardown
docker rm -f rev18-odoo rev18-db; docker network rm rev18net
```

Note: `odoo:18` is Community edition — fine here because the module depends only on the core
`payment` module. The live €1 smoke test needs the live Revolut keys + a public webhook URL
(use a tunnel, or rely on the return route for a quick local check).
