"""CLI entry point for jentic-one."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import sys
from dataclasses import asdict
from getpass import getpass

import structlog
import uvicorn
from fastapi import FastAPI

from jentic_one.admin.services._support.passwords import MIN_PASSWORD_LENGTH
from jentic_one.admin.services.auth_service import AuthService
from jentic_one.admin.services.errors import (
    AdminServiceError,
    SetupAlreadyCompleteError,
    UserEmailNotFoundError,
)
from jentic_one.auth.web.app import install_on_app as _install_auth_verifier
from jentic_one.control.services.key_retirement import KeyRetirementService
from jentic_one.control.services.toolkit_export import ToolkitExportError, ToolkitExportService
from jentic_one.control.services.toolkit_flattening import Finding, ToolkitFlatteningService
from jentic_one.shared.config import AppConfig, load_config
from jentic_one.shared.context import Context
from jentic_one.shared.logging import configure_logging
from jentic_one.shared.metrics import configure_metrics
from jentic_one.shared.tracing import configure_tracing
from jentic_one.shared.web.app_factory import SURFACE_MODULES, create_combined_app
from jentic_one.wiring import build_default_container
from jentic_one.wiring import install_broker_registry_resolver as _install_broker_registry_resolver

SURFACE_DB_DEPS: dict[str, set[str]] = {
    # Auth reaches the control DB read-only to resolve toolkit-binding names for
    # the /me whoami response (issue #686): the binding row lives in the admin DB
    # but the toolkit name lives in the control DB.
    "auth": {"admin", "control"},
    "broker": {"admin", "control", "registry"},
    # Every standalone surface in SURFACES_NEEDING_AUTH verifies callers locally
    # via the auth verifier (_install_auth_verifier), whose ApiKeyResolver and
    # PermissionService resolve API keys / permissions against the admin DB.
    # Without this, a standalone control/registry process crashes at boot with
    # "Access to 'admin' database is not allowed in this context" — the failure
    # mode behind the parts-mode Helm smoke timeouts.
    "control": {"admin"},
    "registry": {"admin"},
}

SURFACES_NEEDING_AUTH: set[str] = {"admin", "control", "registry", "broker"}


def _expand_allowed_dbs(apps: list[str], config: AppConfig) -> set[str]:
    """Expand surface list into the full set of required DB names."""
    allowed = set(apps)
    for surface in apps:
        allowed |= SURFACE_DB_DEPS.get(surface, set())
    # The /mcp mount's search/inspect/catalog tools call the registry services
    # in-process, so a control-plane process serving the
    # mount needs the registry DB even when the registry surface itself is
    # deployed elsewhere. Gated on the flag: with MCP off nothing widens.
    if "control" in apps and config.server.mcp.enabled:
        allowed.add("registry")
    return allowed


def _build_app(ctx: Context, apps: list[str]) -> FastAPI:
    """Build the appropriate FastAPI application based on enabled surfaces."""
    if "broker" in apps and len(apps) > 1:
        raise RuntimeError("broker must run as the sole surface; do not bundle it with others")
    container = build_default_container(ctx)
    if len(apps) == 1:
        surface = apps[0]
        mod = importlib.import_module(SURFACE_MODULES[surface])
        # Standalone control carries the composition container so the /mcp
        # mount (installer + session-manager lifespan) rides it; standalone
        # auth carries it for the /mcp discovery-challenge placeholder (the
        # discovery pointers it serves must not dangle) — the other surfaces
        # keep their own default wiring.
        if surface in ("control", "auth"):
            app: FastAPI = mod.create_app(ctx, container=container)
        else:
            app = mod.create_app(ctx)
        if surface in SURFACES_NEEDING_AUTH and not hasattr(app.state, "verify_token"):
            _install_auth_verifier(app, ctx)
        if surface == "broker" and ctx.is_db_allowed("registry"):
            _install_broker_registry_resolver(app, ctx)
        return app
    app = create_combined_app(ctx, apps, container=container)
    if "broker" in apps and ctx.is_db_allowed("registry"):
        _install_broker_registry_resolver(app, ctx)
    return app


def _service_name() -> str:
    return os.getenv("OTEL_SERVICE_NAME", "jentic-one")


def create_app() -> FastAPI:
    """Factory entry point for uvicorn (used with `factory=True` for reload mode).

    Loads configuration from the environment on each invocation so reload
    workers pick up the same settings as the parent process.
    """
    config = load_config()
    configure_logging(config)
    configure_tracing(_service_name(), config.observability.tracing)
    configure_metrics(_service_name(), config.observability.metrics)
    apps = config.apps
    ctx = Context(config, allowed_dbs=_expand_allowed_dbs(apps, config))
    return _build_app(ctx, apps)


def _serve() -> None:
    """Load config, build context, and run the server."""
    config = load_config()
    # Reload mode spawns multiple uvicorn worker processes. A SQLite admin DB is a
    # single file that does not support concurrent writer processes, so reload
    # against it reintroduces the `database is locked` contention this fix targets
    # (see issue #648). Honour reload only when the admin DB is not SQLite. The
    # guard intentionally inspects only the *admin* backend — that is #648's
    # token-mint path; a SQLite registry/control DB under reload would still
    # contend, but that is out of scope here.
    reload_enabled = config.server.reload
    if reload_enabled and config.databases.admin.backend == "sqlite":
        # Configure logging up front so the warning is emitted in the standard
        # format; the reload branch returns before the single-process path, while
        # the fallthrough below skips re-configuring when we've already done so.
        configure_logging(config)
        logger = structlog.get_logger(__name__)
        logger.warning(
            "reload_disabled_sqlite_admin_db",
            detail=(
                "server.reload ignored: a SQLite admin DB does not support multiple "
                "writer processes; running a single worker instead."
            ),
        )
        reload_enabled = False
        logging_configured = True
    else:
        logging_configured = False

    if reload_enabled:
        uvicorn.run(
            "jentic_one.__main__:create_app",
            host=config.server.host,
            port=config.server.port,
            reload=True,
            factory=True,
        )
        return

    if not logging_configured:
        configure_logging(config)
    configure_tracing(_service_name(), config.observability.tracing)
    configure_metrics(_service_name(), config.observability.metrics)
    apps = config.apps
    ctx = Context(config, allowed_dbs=_expand_allowed_dbs(apps, config))
    app = _build_app(ctx, apps)
    uvicorn.run(
        app,
        host=config.server.host,
        port=config.server.port,
    )


async def _create_admin(
    *,
    email: str | None,
    first_name: str,
    last_name: str,
    password: str | None,
) -> int:
    """Create the first admin user (one-time first-run setup).

    Prompts interactively for any missing field. Password may also be supplied
    on stdin (when not a TTY) so operator wrappers like ``jenticctl setup`` can
    pipe it without it landing in shell history or the process table.
    """
    config = load_config()
    configure_logging(config)

    if email is None:
        email = input("Admin email: ").strip()
    if not email:
        print("error: email is required", file=sys.stderr)
        return 2

    if password is None:
        if sys.stdin.isatty():
            password = getpass(f"Admin password (min {MIN_PASSWORD_LENGTH} chars): ")
            confirm = getpass("Confirm password: ")
            if password != confirm:
                print("error: passwords do not match", file=sys.stderr)
                return 2
        else:
            password = sys.stdin.readline().rstrip("\n")
    if len(password) < MIN_PASSWORD_LENGTH:
        print(
            f"error: password must be at least {MIN_PASSWORD_LENGTH} characters",
            file=sys.stderr,
        )
        return 2

    async with Context(config, allowed_dbs={"admin"}) as ctx:
        auth_svc = AuthService(ctx)
        try:
            await auth_svc.bootstrap_admin(
                email=email,
                password=password,
                first_name=first_name,
                last_name=last_name,
            )
        except SetupAlreadyCompleteError:
            print(
                "error: setup already complete — an admin account already exists. "
                "Use the admin UI to manage users.",
                file=sys.stderr,
            )
            return 3
        except AdminServiceError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    print(f"==> Admin account created for {email}. You can now sign in.", flush=True)
    return 0


async def _reset_password(
    *,
    email: str | None,
    password: str | None,
) -> int:
    """Operator-initiated password reset for an existing user.

    Sets a temporary password and forces the user to change it at next login
    (``must_change_password``). The operator never learns the user's standing
    password. Password may be supplied on stdin (when not a TTY) so wrappers like
    ``jenticctl reset-password`` can pipe it without it landing in shell history
    or the process table.
    """
    config = load_config()
    configure_logging(config)

    if email is None:
        email = input("User email: ").strip()
    if not email:
        print("error: email is required", file=sys.stderr)
        return 2

    if password is None:
        if sys.stdin.isatty():
            password = getpass(f"Temporary password (min {MIN_PASSWORD_LENGTH} chars): ")
            confirm = getpass("Confirm password: ")
            if password != confirm:
                print("error: passwords do not match", file=sys.stderr)
                return 2
        else:
            password = sys.stdin.readline().rstrip("\n")
    if len(password) < MIN_PASSWORD_LENGTH:
        print(
            f"error: password must be at least {MIN_PASSWORD_LENGTH} characters",
            file=sys.stderr,
        )
        return 2

    async with Context(config, allowed_dbs={"admin"}) as ctx:
        auth_svc = AuthService(ctx)
        try:
            await auth_svc.reset_password(email=email, temporary_password=password)
        except UserEmailNotFoundError:
            print(f"error: no user found with email {email}", file=sys.stderr)
            return 3
        except AdminServiceError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    print(
        f"==> Temporary password set for {email}. They must change it at next sign-in.",
        flush=True,
    )
    return 0


async def _retire_toolkit_keys(*, owner_email: str | None) -> int:
    """Run the theme-5 Phase 4 toolkit-key retirement job.

    Converts every resolvable ``jntc_live_`` key into a service account
    carrying exactly ``capabilities:execute``; the unchanged plaintext keeps
    authenticating as that account. One JSONL report line per key goes to
    stdout. Idempotent — safe to re-run after a partial failure.
    """
    config = load_config()
    configure_logging(config)

    async with Context(config, allowed_dbs={"admin", "control"}) as ctx:
        svc = KeyRetirementService(ctx)
        try:
            outcomes = await svc.run(fallback_owner_email=owner_email)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    for outcome in outcomes:
        print(json.dumps(asdict(outcome)), flush=True)

    migrated = sum(1 for o in outcomes if o.action == "migrated")
    skipped = [o for o in outcomes if o.action == "skipped"]
    print(
        f"==> {migrated} key(s) migrated, "
        f"{sum(1 for o in outcomes if o.action == 'already_migrated')} already migrated, "
        f"{len(skipped)} skipped.",
        file=sys.stderr,
        flush=True,
    )
    if any(o.reason == "owner_unresolved" for o in skipped):
        print(
            "==> Some keys have no resolvable owner; re-run with "
            "--owner <admin-email> to assign them.",
            file=sys.stderr,
            flush=True,
        )
        return 3
    return 0


def _write_report(findings: list[Finding], report_path: str | None) -> None:
    """Emit one JSON line per finding, to ``report_path`` or stdout."""
    if report_path is None:
        for finding in findings:
            print(json.dumps(finding.as_dict()), flush=True)
        return
    with open(report_path, "w", encoding="utf-8") as fh:
        for finding in findings:
            fh.write(json.dumps(finding.as_dict()) + "\n")


async def _flatten_toolkits(
    *,
    diff_only: bool,
    report_path: str | None,
    verify: bool,
    acknowledge: bool,
) -> int:
    """Run the theme-5 Phase 6a flattening job (or its verify mode).

    Default mode derives direct agent↔credential bindings from the toolkit
    graph (idempotent — re-run and confirm zero creations, the
    double-run-and-diff check). ``--verify`` runs the R-02 queries instead;
    ``--verify --acknowledge`` additionally writes the sentinel row Phase
    6b's drop migrations require. One JSONL report line per finding.
    """
    config = load_config()
    configure_logging(config)

    async with Context(config, allowed_dbs={"admin", "control"}) as ctx:
        svc = ToolkitFlatteningService(ctx)
        if verify:
            result = await svc.verify(acknowledge=acknowledge)
            _write_report(result.findings, report_path)
            print(
                f"==> verify {'PASSED' if result.passed else 'FAILED'}: "
                f"{result.legacy_pair_count} legacy pair(s), "
                f"{result.direct_binding_count} direct binding(s), "
                f"{result.missing_pair_count} missing, "
                f"{len(result.findings)} report line(s).",
                file=sys.stderr,
                flush=True,
            )
            if acknowledge:
                print(
                    "==> acknowledgement recorded — Phase 6b drops are unblocked."
                    if result.acknowledged
                    else "==> acknowledgement REFUSED: verification failed; run "
                    "flatten-toolkits first, then re-verify.",
                    file=sys.stderr,
                    flush=True,
                )
            return 0 if result.passed else 1

        run = await svc.run(diff_only=diff_only)
        _write_report(run.findings, report_path)
        verb = "would create" if diff_only else "created"
        print(
            f"==> {run.pairs_total} legacy pair(s): {verb} {run.created} binding(s), "
            f"{run.already_present} already present, {len(run.findings)} report line(s).",
            file=sys.stderr,
            flush=True,
        )
        if not diff_only:
            print(
                "==> Re-run this command and confirm it reports zero creations "
                "(double-run-and-diff), then run with --verify.",
                file=sys.stderr,
                flush=True,
            )
    return 0


async def _export_toolkits(*, out_path: str | None, import_path: str | None) -> int:
    """Export the five legacy toolkit tables, or re-import an export file."""
    config = load_config()
    configure_logging(config)

    async with Context(config, allowed_dbs={"admin", "control"}) as ctx:
        svc = ToolkitExportService(ctx)
        if import_path is not None:
            try:
                with open(import_path, encoding="utf-8") as fh:
                    document = json.load(fh)
            except (OSError, json.JSONDecodeError) as exc:
                print(f"error: cannot read {import_path}: {exc}", file=sys.stderr)
                return 2
            try:
                outcome = await svc.import_document(document)
            except ToolkitExportError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
            for table in sorted(outcome.inserted):
                print(
                    f"==> {table}: {outcome.inserted[table]} inserted, "
                    f"{outcome.skipped_existing[table]} already present.",
                    file=sys.stderr,
                    flush=True,
                )
            return 0

        document = await svc.export()
        assert out_path is not None  # argparse enforces the either/or
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(document, fh)
            fh.write("\n")
        counts = ", ".join(
            f"{table}={body['row_count']}" for table, body in document["tables"].items()
        )
        print(
            f"==> exported to {out_path} ({counts}). The file embeds key hash "
            "digests — store it like a secrets backup.",
            file=sys.stderr,
            flush=True,
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    """Dispatch CLI subcommands. With no subcommand, run the server."""
    parser = argparse.ArgumentParser(prog="jentic_one", description="jentic-one service CLI.")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("serve", help="Run the HTTP server (default).")

    create_admin = sub.add_parser(
        "create-admin",
        help="Create the first admin user (one-time first-run setup).",
    )
    create_admin.add_argument("--email", help="Admin email (prompted if omitted).")
    create_admin.add_argument(
        "--password",
        help="Admin password (prompted, or read from stdin when non-interactive, if omitted).",
    )
    create_admin.add_argument("--first-name", default="Admin", help="First name (default: Admin).")
    create_admin.add_argument("--last-name", default="User", help="Last name (default: User).")

    reset_password = sub.add_parser(
        "reset-password",
        help="Set a temporary password for an existing user (forces change at next sign-in).",
    )
    reset_password.add_argument("--email", help="User email (prompted if omitted).")
    reset_password.add_argument(
        "--password",
        help="Temporary password (prompted, or read from stdin when non-interactive, if omitted).",
    )

    retire_keys = sub.add_parser(
        "retire-toolkit-keys",
        help=("Migrate jntc_live_ toolkit keys to service accounts (theme-5 Phase 4; idempotent)."),
    )
    retire_keys.add_argument(
        "--owner",
        help=(
            "Email of the user to own service accounts whose toolkit key has "
            "no resolvable creator (such keys are skipped and reported otherwise)."
        ),
    )

    flatten = sub.add_parser(
        "flatten-toolkits",
        help=(
            "Derive direct agent-credential bindings from the toolkit graph "
            "(theme-5 Phase 6a; idempotent, operator-invoked)."
        ),
    )
    flatten.add_argument(
        "--diff-only",
        action="store_true",
        help="Report what a run would create without writing anything.",
    )
    flatten.add_argument(
        "--report",
        metavar="PATH",
        help="Write the JSONL report here instead of stdout.",
    )
    flatten.add_argument(
        "--verify",
        action="store_true",
        help=(
            "Run the verification queries instead of flattening: every legacy "
            "(agent, credential) pair must exist as a direct binding."
        ),
    )
    flatten.add_argument(
        "--acknowledge",
        action="store_true",
        help=(
            "With --verify: record the operator acknowledgement that gates the "
            "Phase-6b drop migrations. Refused unless the verification passes "
            "in this same invocation."
        ),
    )

    export_toolkits = sub.add_parser(
        "export-toolkits",
        help=(
            "Export the five legacy toolkit tables to a re-importable JSON file "
            "(theme-5 Phase 6a; the only row-restoring rollback after Phase 6b)."
        ),
    )
    export_group = export_toolkits.add_mutually_exclusive_group(required=True)
    export_group.add_argument("--out", metavar="PATH", help="Write the export file here.")
    export_group.add_argument(
        "--import",
        dest="import_path",
        metavar="PATH",
        help="Re-import a previously exported file (idempotent by row id).",
    )

    args = parser.parse_args(argv)

    if args.command == "create-admin":
        return asyncio.run(
            _create_admin(
                email=args.email,
                first_name=args.first_name,
                last_name=args.last_name,
                password=args.password,
            )
        )

    if args.command == "reset-password":
        return asyncio.run(
            _reset_password(
                email=args.email,
                password=args.password,
            )
        )

    if args.command == "retire-toolkit-keys":
        return asyncio.run(_retire_toolkit_keys(owner_email=args.owner))

    if args.command == "flatten-toolkits":
        if args.acknowledge and not args.verify:
            flatten.error("--acknowledge requires --verify (it records a passed verification)")
        if args.diff_only and args.verify:
            flatten.error("--diff-only and --verify are mutually exclusive")
        return asyncio.run(
            _flatten_toolkits(
                diff_only=args.diff_only,
                report_path=args.report,
                verify=args.verify,
                acknowledge=args.acknowledge,
            )
        )

    if args.command == "export-toolkits":
        return asyncio.run(_export_toolkits(out_path=args.out, import_path=args.import_path))

    _serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
