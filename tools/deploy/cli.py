"""Deploy CLI — click-based replacement for Makefile k8s/Helm targets."""

from __future__ import annotations

import os

import click

from .config import (
    HELM_DIR,
    HELM_TIMEOUT,
    IMG_PREFIX,
    KIND_CLUSTER,
    MODE_SERVICES,
    MONITORING_NS,
    NAMESPACE,
    OBS_RELEASE,
    RELEASE,
    REPO_ROOT,
    SERVICES,
    get_version,
    mode_values_file,
    obs_values_file,
    otel_values_file,
    prom_values_file,
)
from .runner import preflight, run

MODE_CHOICE = click.Choice(["combined", "parts", "broker"])


@click.group()
def cli() -> None:
    """Local Kubernetes deployment tooling for jentic-one."""


# ---------- cluster subgroup ----------


@cli.group()
def cluster() -> None:
    """Kind cluster lifecycle."""


@cluster.command("up")
@click.option("--name", default=KIND_CLUSTER, help="Cluster name.")
def cluster_up(name: str) -> None:
    """Create a local kind cluster."""
    preflight("kind")
    config = str(REPO_ROOT / "deploy" / "k8s" / "kind-config.yaml")
    run("kind", "create", "cluster", "--name", name, "--config", config)


@cluster.command("down")
@click.option("--name", default=KIND_CLUSTER, help="Cluster name.")
def cluster_down(name: str) -> None:
    """Delete a local kind cluster."""
    preflight("kind")
    run("kind", "delete", "cluster", "--name", name)


# ---------- load ----------


@cli.command()
@click.option("--mode", type=MODE_CHOICE, default="combined", help="Deployment mode.")
@click.option("--all", "load_all", is_flag=True, help="Load all service images.")
@click.option("--cluster", "cluster_name", default=KIND_CLUSTER, help="Kind cluster name.")
def load(mode: str, load_all: bool, cluster_name: str) -> None:
    """Load Docker images into the kind cluster."""
    preflight("kind")
    version = get_version()
    services = SERVICES if load_all else MODE_SERVICES[mode]
    for svc in services:
        run("kind", "load", "docker-image", f"{IMG_PREFIX}/{svc}:{version}", "--name", cluster_name)


# ---------- helm-deps ----------


@cli.command("helm-deps")
def helm_deps() -> None:
    """Update Helm chart dependencies."""
    preflight("helm")
    umbrella = HELM_DIR / "jentic-one"
    for svc in ("app", "broker", "registry", "admin", "control"):
        run("helm", "dependency", "build", str(umbrella / "charts" / svc))
    run("helm", "dependency", "update", str(umbrella))


# ---------- port-forward ----------


@cli.command("port-forward")
@click.option("--mode", type=MODE_CHOICE, default="combined", help="Deployment mode.")
def port_forward(mode: str) -> None:
    """Port-forward the gateway service to localhost:8000 (parts mode only)."""
    if mode != "parts":
        click.echo("Port-forward is only needed in parts mode (gateway is exposed via NodePort).")
        return
    preflight("kubectl")
    run(
        "kubectl",
        "-n",
        NAMESPACE,
        "port-forward",
        f"svc/{RELEASE}-gateway",
        "8000:8000",
        check=False,
    )


# ---------- up (deploy/redeploy) ----------


@cli.command()
@click.option("--mode", type=MODE_CHOICE, default="combined", help="Deployment mode.")
@click.option(
    "--obs/--no-obs",
    default=True,
    help="Deploy the observability stack alongside the app (default: on).",
)
@click.option("--otel/--no-otel", default=True, help="Wire OTel sidecars to obs stack.")
@click.option(
    "--metrics",
    type=click.Choice(["prometheus", "none"]),
    default="none",
    help="Metrics exporter.",
)
@click.option("--timeout", default=HELM_TIMEOUT, help="Helm timeout.")
@click.option("--cluster", "cluster_name", default=KIND_CLUSTER, help="Kind cluster name.")
@click.pass_context
def up(
    ctx: click.Context,
    mode: str,
    obs: bool,
    otel: bool,
    metrics: str,
    timeout: str,
    cluster_name: str,
) -> None:
    """Deploy (or upgrade) to the local kind cluster."""
    preflight("helm", "kind", "kubectl")
    version = get_version()

    if obs:
        ctx.invoke(obs_up_cmd, timeout=timeout)

    ctx.invoke(load, mode=mode, load_all=False, cluster_name=cluster_name)
    ctx.invoke(helm_deps)

    cmd = [
        "helm",
        "upgrade",
        "--install",
        RELEASE,
        str(HELM_DIR / "jentic-one"),
        "--namespace",
        NAMESPACE,
        "--create-namespace",
        "-f",
        str(mode_values_file(mode)),
    ]
    if otel:
        cmd += ["-f", str(otel_values_file())]
    if metrics == "prometheus":
        cmd += ["-f", str(prom_values_file())]
    cmd += ["--set", f"global.image.tag={version}", "--wait", "--timeout", timeout]
    run(*cmd)


# ---------- down ----------


@cli.command()
def down() -> None:
    """Remove the Helm release from the local cluster."""
    preflight("helm")
    run("helm", "uninstall", RELEASE, "--namespace", NAMESPACE)


# ---------- smoke ----------


@cli.command()
@click.option("--mode", type=MODE_CHOICE, default="combined", help="Deployment mode.")
@click.option("--obs/--no-obs", default=False, help="Run observability assertions.")
def smoke(mode: str, obs: bool) -> None:
    """Run smoke tests against the local cluster."""
    base_url = "http://localhost:8080" if mode == "broker" else "http://localhost:8000"
    env = {
        **os.environ,
        "MODE": mode,
        "BASE_URL": base_url,
        "OBS": "1" if obs else "",
        # The smoke-upstream harness as seen by the broker (in-cluster DNS) and by
        # the test runner (kind host port). These match the deploy fixture
        # defaults, but are exported explicitly so non-default clusters work too.
        "UPSTREAM_INCLUSTER_URL": "http://jentic-smoke-upstream:8084",
        "UPSTREAM_DIRECT_URL": "http://localhost:8084",
    }
    run("uv", "run", "pytest", "tests/smoke/", "-m", "smoke", "--no-cov", env=env)


# ---------- ci-smoke ----------


@cli.command("ci-smoke")
@click.option("--mode", type=MODE_CHOICE, default="combined", help="Deployment mode.")
@click.option("--otel/--no-otel", default=False, help="Bring up the obs stack.")
@click.option("--timeout", default=HELM_TIMEOUT, help="Helm timeout.")
@click.option("--cluster", "cluster_name", default=KIND_CLUSTER, help="Kind cluster name.")
@click.pass_context
def ci_smoke(ctx: click.Context, mode: str, otel: bool, timeout: str, cluster_name: str) -> None:
    """End-to-end CI flow: build, load, deploy, smoke."""
    preflight("make", "kind", "helm")
    services = MODE_SERVICES[mode]

    run("make", "build-base")
    for svc in services:
        run("make", f"build-{svc}")

    ctx.invoke(load, mode=mode, load_all=False, cluster_name=cluster_name)

    if otel:
        ctx.invoke(obs_up_cmd)

    ctx.invoke(
        up,
        mode=mode,
        obs=False,
        otel=otel,
        metrics="none",
        timeout=timeout,
        cluster_name=cluster_name,
    )
    ctx.invoke(smoke, mode=mode, obs=otel)


# ---------- obs subgroup ----------


@cli.group()
def obs() -> None:
    """Observability stack lifecycle."""


@obs.command("deps")
def obs_deps() -> None:
    """Update observability chart dependencies."""
    preflight("helm")
    run("helm", "dependency", "update", str(HELM_DIR / "observability"))


@obs.command("up")
@click.option("--timeout", default=HELM_TIMEOUT, help="Helm timeout.")
@click.pass_context
def obs_up_cmd(ctx: click.Context, timeout: str = HELM_TIMEOUT) -> None:
    """Deploy Grafana/Loki/Tempo/Prometheus + OTel collector."""
    preflight("helm")
    ctx.invoke(obs_deps)
    run(
        "helm",
        "upgrade",
        "--install",
        OBS_RELEASE,
        str(HELM_DIR / "observability"),
        "--namespace",
        MONITORING_NS,
        "--create-namespace",
        "-f",
        str(obs_values_file()),
        "--wait",
        "--timeout",
        timeout,
    )


@obs.command("down")
def obs_down_cmd() -> None:
    """Remove the observability release."""
    preflight("helm")
    run("helm", "uninstall", OBS_RELEASE, "--namespace", MONITORING_NS)


# ---------- grafana ----------


@cli.command()
def grafana() -> None:
    """Port-forward Grafana to localhost:3000 and open browser."""
    import subprocess as sp
    import threading
    import webbrowser

    preflight("kubectl")
    print("Grafana login: admin / admin (override via values)")
    print("Opening http://localhost:3000 ...")

    threading.Timer(1.0, lambda: webbrowser.open("http://localhost:3000")).start()

    sp.run(
        ["kubectl", "-n", MONITORING_NS, "port-forward", f"svc/{OBS_RELEASE}-grafana", "3000:80"],
        check=False,
    )


# ---------- logs ----------


@cli.command()
@click.argument("service")
@click.option("--ns", default=NAMESPACE, help="Kubernetes namespace.")
def logs(service: str, ns: str) -> None:
    """Tail logs for a service."""
    preflight("kubectl")
    run(
        "kubectl",
        "logs",
        "-n",
        ns,
        "-l",
        f"app.kubernetes.io/name={service}",
        "-f",
        "--tail=100",
        check=False,
    )


# ---------- ui ----------


@cli.command()
@click.option(
    "--ns",
    type=click.Choice(["jentic", "monitoring", "all"]),
    default="jentic",
    help="Namespace to scope.",
)
def ui(ns: str) -> None:
    """Open k9s scoped to a namespace."""
    preflight("k9s")
    if ns == "all":
        run("k9s", "-A", check=False)
    elif ns == "monitoring":
        run("k9s", "-n", MONITORING_NS, check=False)
    else:
        run("k9s", "-n", NAMESPACE, check=False)


# ---------- images ----------


@cli.command()
def images() -> None:
    """List locally built jentic-one images."""
    preflight("docker")
    run("docker", "images", f"{IMG_PREFIX}/*")
