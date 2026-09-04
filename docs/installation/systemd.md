# Installing with systemd

The same two containers as the [Docker guide](docker.md), supervised by
systemd on a Linux host: automatic start on boot, restart on failure, and
`journalctl` for logs. Docker (or a compatible engine) must be installed and
running on the host.

## 1. Do the Docker guide's setup first

Follow the [Docker guide](docker.md) through step 5: pull and verify the
image, write `/etc/jentic/production.yaml` and `/etc/jentic/prod.env`, prepare
the database, run migrations, and create the first admin. Then come back here
instead of running the containers by hand.

## 2. Pin the image in one place

The units below read the image reference from a single file, so an upgrade is
a one-line edit:

```bash
# /etc/jentic/image.env
IMAGE=ghcr.io/jentic/jentic-one-app@sha256:<digest-from-the-release>
```

## 3. Install the units

`/etc/systemd/system/jentic-migrate.service` — a oneshot that (re)applies
migrations. The app and broker require it, so migrations always run before
either starts, including after an image bump:

```ini
[Unit]
Description=Jentic One database migrations
After=docker.service network-online.target
Requires=docker.service
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
EnvironmentFile=/etc/jentic/image.env
ExecStart=/usr/bin/docker run --rm --env-file /etc/jentic/prod.env \
  -v /etc/jentic:/etc/jentic:ro \
  ${IMAGE} python -m jentic_one.migrations.run
```

`/etc/systemd/system/jentic-app.service` — the control plane (UI + APIs):

```ini
[Unit]
Description=Jentic One app (control plane)
After=docker.service jentic-migrate.service
Requires=docker.service jentic-migrate.service

[Service]
Restart=always
RestartSec=5
EnvironmentFile=/etc/jentic/image.env
ExecStartPre=-/usr/bin/docker rm -f jentic-app
ExecStart=/usr/bin/docker run --rm --name jentic-app \
  --env-file /etc/jentic/prod.env \
  -v /etc/jentic:/etc/jentic:ro \
  -p 127.0.0.1:8000:8000 \
  ${IMAGE}
ExecStop=/usr/bin/docker stop -t 30 jentic-app

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/jentic-broker.service` — the data plane. Identical apart
from the name, the port, and `JENTIC__APPS=broker` (the broker must run as the
sole surface):

```ini
[Unit]
Description=Jentic One broker (data plane)
After=docker.service jentic-migrate.service
Requires=docker.service jentic-migrate.service

[Service]
Restart=always
RestartSec=5
EnvironmentFile=/etc/jentic/image.env
ExecStartPre=-/usr/bin/docker rm -f jentic-broker
ExecStart=/usr/bin/docker run --rm --name jentic-broker \
  --env-file /etc/jentic/prod.env \
  -e JENTIC__APPS=broker \
  -v /etc/jentic:/etc/jentic:ro \
  -p 127.0.0.1:8100:8000 \
  ${IMAGE}
ExecStop=/usr/bin/docker stop -t 30 jentic-broker

[Install]
WantedBy=multi-user.target
```

## 4. Enable and start

```bash
systemctl daemon-reload
systemctl enable --now jentic-app jentic-broker   # pulls in jentic-migrate first

curl -fsS http://localhost:8000/health   # app
curl -fsS http://localhost:8100/health   # broker
```

The units assume the image is already on the host (step 1's pull or `docker
load`): `docker run` fetches a missing image on demand, which stalls the
first start after image GC (`docker system prune`) and fails outright on an
air-gapped host — either add
`ExecStartPre=/usr/bin/docker pull ${IMAGE}` to the two service units
(connected hosts) or re-run the pull/load step before starting.

Logs go to the journal:

```bash
journalctl -u jentic-app -f
journalctl -u jentic-broker -f
```

As in the Docker guide, both surfaces are plain HTTP bound to loopback — front
them with a TLS-terminating reverse proxy before exposing anything.

## Upgrading

0. Take a [backup](../operations/backup-restore.md) — it is the rollback.
1. Verify the new release's digest (`cosign verify` — see the
   [Docker guide](docker.md#1-pull-and-verify-the-image)) and load or pull the
   image on the host.
2. Edit the digest in `/etc/jentic/image.env`.
3. Restart in order — migrations first, then the services:

```bash
systemctl restart jentic-migrate jentic-app jentic-broker
```

If `jentic-migrate` fails, the app and broker stay stopped rather than serving
on a half-migrated schema (they `Requires=` it) — fix, then re-run the restart.
Note this differs from the compose flow, where a failed migrate leaves the old
containers running.
