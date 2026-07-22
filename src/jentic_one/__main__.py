"""CLI entry point for jentic-one."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import os
import sys
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
from jentic_one.shared.config import load_config
from jentic_one.shared.context import Context
from jentic_one.shared.logging import configure_logging
from jentic_one.shared.metrics import configure_metrics
from jentic_one.shared.tracing import configure_tracing
from jentic_one.shared.web.app_factory import SURFACE_MODULES, create_combined_app
from jentic_one.wiring import install_broker_registry_resolver as _install_broker_registry_resolver

SURFACE_DB_DEPS: dict[str, set[str]] = {
    # Auth reaches the control DB read-only to resolve toolkit-binding names for
    # the /me whoami response (issue #686): the binding row lives in the admin DB
    # but the toolkit name lives in the control DB.
    "auth": {"admin", "control"},
    "broker": {"admin", "control", "registry"},
}

SURFACES_NEEDING_AUTH: set[str] = {"admin", "control", "registry", "broker"}


def _expand_allowed_dbs(apps: list[str]) -> set[str]:
    """Expand surface list into the full set of required DB names."""
    allowed = set(apps)
    for surface in apps:
        allowed |= SURFACE_DB_DEPS.get(surface, set())
    return allowed


def _build_app(ctx: Context, apps: list[str]) -> FastAPI:
    """Build the appropriate FastAPI application based on enabled surfaces."""
    if "broker" in apps and len(apps) > 1:
        raise RuntimeError("broker must run as the sole surface; do not bundle it with others")
    if len(apps) == 1:
        surface = apps[0]
        mod = importlib.import_module(SURFACE_MODULES[surface])
        app: FastAPI = mod.create_app(ctx)
        if surface in SURFACES_NEEDING_AUTH and not hasattr(app.state, "verify_token"):
            _install_auth_verifier(app, ctx)
        if surface == "broker" and ctx.is_db_allowed("registry"):
            _install_broker_registry_resolver(app, ctx)
        return app
    app = create_combined_app(ctx, apps)
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
    ctx = Context(config, allowed_dbs=_expand_allowed_dbs(apps))
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
    ctx = Context(config, allowed_dbs=_expand_allowed_dbs(apps))
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

    _serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
