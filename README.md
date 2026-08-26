**A self-hosted execution layer for AI agents.**  
Connect an agent to any API you need, and enforce exactly what it is allowed to call.

[Quickstart](#quickstart)
[Documentation](#documentation)
[Discussions](https://github.com/jentic/jentic-one/discussions)

> [!NOTE]
> **Public Beta.** Schemas and CLI commands can change between 0.x releases. Pin a version if
> you need stability. Contributions are welcome: see [CONTRIBUTING.md](CONTRIBUTING.md) and the
> [open issues](https://github.com/jentic/jentic-one/issues).

## What Jentic One is

Giving an agent API access normally means giving it an API key. Jentic One removes that step.
Register the APIs an agent may use, store the credentials once, and the agent makes its calls
through the Broker. The Broker checks the agent's permissions, attaches the credential at
execution time, and writes an audit record. Your agent never sees your keys.

## Quickstart

### Self-hosted (Build local)

```bash
# Build and start service
git clone https://github.com/jentic/jentic-one.git && cd jentic-one
make install   # install dependencies and git hooks
make dev       # idempotent local bring-up: fixtures + migrations + UI, then run the app
open http://127.0.0.1:8000/app/setup

# Build CLI's
cd cli && make build
./jenticctl    # Jentic CTL (Control, Admin of the System)
./jentic       # Jentic CLI (search, inspect, execute - agent entrypoint)
```

### Self-hosted (Guided installation)

```bash
curl -fsSL https://raw.githubusercontent.com/jentic/jentic-one/main/tools/install.sh | sh
```

### Self-hosted(Docker)

```bash
# Download the Jentic One App and Broker
docker pull ghcr.io/jentic/jentic-one-app:latest

# Minimal config needed (all options: docs/reference/config.md)
cat > jentic-one.yaml <<EOF
databases:
  registry: {backend: sqlite, path: /data/registry.db, schema_name: registry}
  control:  {backend: sqlite, path: /data/control.db,  schema_name: control}
  admin:    {backend: sqlite, path: /data/admin.db,    schema_name: admin}
server: {host: 0.0.0.0, port: 8000}
credentials:
  encryption:
    active_id: v1
    entries:
      - id: v1
        material: "$(openssl rand -base64 32)"
EOF

# Initialise the databases (also the upgrade step — re-run it on a new image)
docker run --rm \
  -v jentic-data:/data \
  -v "$PWD/jentic-one.yaml":/etc/jentic/jentic-one.yaml:ro \
  -e JENTIC_CONFIG_FILE=/etc/jentic/jentic-one.yaml \
  ghcr.io/jentic/jentic-one-app:latest python -m jentic_one.migrations.run

# Start the Control Plane
docker run -d --name jentic-app \
  -v jentic-data:/data \
  -v "$PWD/jentic-one.yaml":/etc/jentic/jentic-one.yaml:ro \
  -e JENTIC_CONFIG_FILE=/etc/jentic/jentic-one.yaml \
  -p 127.0.0.1:8000:8000 ghcr.io/jentic/jentic-one-app:latest

# Start the Broker
docker run -d --name jentic-broker -v jentic-data:/data \
  -v "$PWD/jentic-one.yaml":/etc/jentic/jentic-one.yaml:ro \
  -e JENTIC_CONFIG_FILE=/etc/jentic/jentic-one.yaml -e JENTIC__APPS=broker \
  -p 127.0.0.1:8100:8000 ghcr.io/jentic/jentic-one-app:latest

# Open Web UI and create admin account
open http://127.0.0.1:8000/app/setup
```

[More information on Local Installations](docs/development/local-setup.md).

### Managed Install

AWS Marketplace (*coming soon*)
For Commercial use get in touch [jentic.com/contact](https://jentic.com/contact).

## How it works

Jentic One handles secure third-party API execution for agents. It deploys as two peer units
above a shared database. **App** is the control plane and contains the Registry, Control and
Admin surfaces. **Broker** is the data plane. Configuration happens through App; the agent
talks only to the Broker.

On each call the Broker checks the agent's permissions, attaches the stored credential,
forwards the request, and writes an audit record. The credential is added inside the Broker,
after the permission check, and is never returned to the caller.

## Why we built Jentic One


## Documentation

CLI: Full reference: `[cli/README.md](cli/README.md)`.
SDK (Go): 


Basic index list to docs TODO

## Contributing

Commit messages follow Conventional Commits, and `make check` must pass before opening a pull
request. [CONTRIBUTING.md](CONTRIBUTING.md) has the full workflow, the
[open issues](https://github.com/jentic/jentic-one/issues) list where help is wanted, and the
Jentic [Code of Conduct](https://github.com/jentic/.github/blob/main/CODE_OF_CONDUCT.md)
applies.

Use [Discussions](https://github.com/jentic/jentic-one/discussions) for questions and proposals.

## Links

[Jentic One](https://jentic.com/jentic-one) ·
[API Directory](https://github.com/jentic/jentic-public-apis) ·

## License

Jentic One is licensed under the [Apache 2.0](LICENSE).
