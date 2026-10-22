"""The governed git pipeline: clones, fetches and pushes to github.com through Jentic.

A vendor CLI's git (relayed by the egress proxy with the command's GitHub
invocation token) reaches the broker at the CLI proxy route with a
``https://github.com/<owner>/<repo>.git/...`` smart-HTTP URL. It runs here, not
through the execute pipeline (which needs a registry operation, buffers bodies
and caps them at a few MiB):

1. the agent's binding for the built-in git API (``broker.core.git_api``) is
   derived and its credential selected, as for any API;
2. the binding's git rules (``shared.permissions.git_rules``) decide the
   request: a fetch, the push advertisement, or each ref update of a push;
3. only then is the credential decrypted and injected as git's Basic auth;
4. the request streams to ``https://github.com`` and the response streams
   back: the ref advertisement with ``refs/jentic/*`` and ``push-cert``
   removed, a pack or a push's report-status unchanged.

A push is read in full first (spooled to a bounded temporary file), so its
ref commands and pack can be checked: the hard ref limits, the rules per ref,
and the force-push check (``broker.core.git_push``, ``broker.core.git_pack``).
A refused push is answered with git's own report-status (``ng <ref> jentic:
<reason>``) without reaching GitHub, so git prints each reason; an allowed
push forwards the client's original body once.

Every served request leaves one execution record (persisted after the
response body ends, see :meth:`GitPipeline.record`); a refusal by the rules
also records ``broker.pbac_denied`` naming the repository, ref and reason.
"""

from __future__ import annotations

import asyncio
import gzip
import tempfile
import time
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import IO, BinaryIO, cast

import structlog

from jentic_one.broker.adapters.runners.git import GitTimeouts, GitUpstream, GitUpstreamResponse
from jentic_one.broker.core.denial import DenialReason
from jentic_one.broker.core.exceptions import (
    ActionDeniedError,
    BrokerError,
    GitCredentialUnsupportedError,
    GitRequestRejectedError,
    OperationNotFoundError,
    PayloadTooLargeError,
    UpstreamTimeoutError,
    UpstreamTransportError,
    git_action_denied_directive,
    git_credential_unsupported_directive,
    git_request_rejected_directive,
)
from jentic_one.broker.core.execution import mint_execution_id
from jentic_one.broker.core.git_api import (
    GITHUB_GIT_API,
    RECEIVE_PACK_OPERATION,
    bearer_token,
    git_basic_authorization,
    operation_for,
)
from jentic_one.broker.core.git_pack import PackError, PackIndex, index_pack, walk_to_boundaries
from jentic_one.broker.core.git_protocol import (
    GIT_UPSTREAM_HOST,
    AdvertisementFilter,
    GitEndpoint,
    GitProtocolError,
    GitService,
    GitTarget,
    ReceivePackHead,
    denied_report_status,
    is_flush_only,
    parse_receive_pack_head,
    ref_hard_deny_reason,
)
from jentic_one.broker.core.git_push import (
    NOT_APPLIED_REASON,
    RefPlan,
    non_fast_forward_reason,
    plan_command,
    push_too_large_reason,
    rules_denied_reason,
    thin_pack_reason,
    too_many_checks_reason,
    unknown_commit_reason,
    unreadable_pack_reason,
    unverified_reason,
)
from jentic_one.broker.core.headers import JenticHeader
from jentic_one.broker.services.credentials.orchestrator import CredentialService
from jentic_one.broker.services.credentials.resolver import ResolvedCredential
from jentic_one.broker.services.execution.authorization import (
    binding_api_role,
    derive_credential_bindings,
    record_pbac_denial,
)
from jentic_one.broker.services.execution.service import persist_streaming_execution
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.broker.execution import StreamingOutcome
from jentic_one.shared.broker.protocols import (
    AgentRuleEvaluatorProtocol,
    CredentialDeriverProtocol,
    GitEvaluation,
)
from jentic_one.shared.broker.schemas import ExecuteRequestContext
from jentic_one.shared.config import CliFacadeGitConfig
from jentic_one.shared.context import Context
from jentic_one.shared.models import ExecutionStatus
from jentic_one.shared.permissions.git_rules import GitAction, GitItem

logger = structlog.get_logger(__name__)

_CHUNK = 64 * 1024
#: Cap on a compare API response body (its commit list is cut to one entry).
_COMPARE_MAX_BYTES = 8 * 1024 * 1024
#: At most this many ``broker.pbac_denied`` events per refused push.
_MAX_DENIAL_EVENTS = 20
_MAX_RECORDED_ERROR = 500


@dataclass(frozen=True, slots=True)
class GitCall:
    """One git smart-HTTP request, as the route read it.

    ``trace_id`` and ``credential_name`` come from the invocation token;
    ``content_type``, ``content_encoding`` and ``user_agent`` are the
    client's request headers; ``instance`` is the request path problems name.
    """

    target: GitTarget
    identity: Identity
    trace_id: str
    instance: str
    credential_name: str | None = None
    content_type: str | None = None
    content_encoding: str | None = None
    user_agent: str | None = None


@dataclass(slots=True)
class GitReply:
    """The response to stream back to git, and what to record once it ends.

    ``record`` is ``None`` for a reply nothing is recorded for (git's
    authentication probe before a large push).
    """

    status_code: int
    media_type: str
    body: AsyncIterator[bytes]
    headers: dict[str, str]
    outcome: StreamingOutcome
    started_at: datetime
    identity: Identity
    record: ExecuteRequestContext | None
    status: ExecutionStatus = ExecutionStatus.COMPLETED


@dataclass(frozen=True, slots=True)
class _Binding:
    credential: ResolvedCredential
    allowed_credential_ids: list[str]
    rule_set_id: str | None


@dataclass(frozen=True, slots=True)
class _Auth:
    """The injected credential: git's Basic header, the compare API's bearer header."""

    git: str
    api: str
    credential_id: str | None
    credential_name: str | None


@dataclass(frozen=True, slots=True)
class _Denial:
    reason: str
    code: DenialReason
    action: GitAction | None = None


@dataclass(slots=True)
class _Spool:
    file: IO[bytes]
    size: int = 0
    too_large: bool = False
    head: bytes = b""


@dataclass(slots=True)
class _Budget:
    left: int


@dataclass(slots=True)
class _Inspection:
    head: ReceivePackHead
    index: PackIndex | None = None
    pack_error: str | None = None
    refs: list[str] = field(default_factory=list)


class _CappedReader:
    """A reader that refuses to return more than ``limit`` bytes in total."""

    def __init__(self, inner: IO[bytes], limit: int) -> None:
        self._inner = inner
        self._left = limit

    def read(self, size: int = -1) -> bytes:
        data = self._inner.read(size)
        self._left -= len(data)
        if self._left < 0:
            raise PackError("the push inflates past the size limit")
        return data


def _inspect_push(
    spool: IO[bytes],
    *,
    content_encoding: str | None,
    cfg: CliFacadeGitConfig,
    skip_pack: bool,
) -> _Inspection:
    """Read a spooled push: its ref commands, then (unless ``skip_pack``) its pack.

    Synchronous; runs in a worker thread. Raises :class:`GitProtocolError` for
    commands that cannot be read; a pack that cannot be read is reported on
    the result instead, so every ref can be refused in report-status.
    """
    spool.seek(0)
    source: IO[bytes] = spool
    if content_encoding == "gzip":
        source = cast(IO[bytes], gzip.GzipFile(fileobj=spool, mode="rb"))
    reader = cast(BinaryIO, _CappedReader(source, cfg.max_push_bytes))
    try:
        head = parse_receive_pack_head(reader, max_commands=cfg.max_ref_updates)
    except (OSError, EOFError, PackError) as exc:
        raise GitProtocolError("the push body could not be read") from exc
    inspection = _Inspection(head=head)
    if skip_pack or not head.needs_pack:
        return inspection
    try:
        inspection.index = index_pack(reader, max_commit_bytes=cfg.max_inspected_commit_bytes)
    except PackError as exc:
        inspection.pack_error = str(exc)
    except (OSError, EOFError):
        inspection.pack_error = "the pack is not valid gzip data"
    return inspection


class GitPipeline:
    """Serves one git smart-HTTP request (see the module doc)."""

    def __init__(
        self,
        ctx: Context,
        *,
        credential_deriver: CredentialDeriverProtocol,
        rule_evaluator: AgentRuleEvaluatorProtocol,
        upstream: GitUpstream,
    ) -> None:
        self._ctx = ctx
        self._deriver = credential_deriver
        self._rules = rule_evaluator
        self._upstream = upstream
        self._cfg = ctx.config.cli_facades.git
        self._timeouts = GitTimeouts(
            connect_s=self._cfg.connect_timeout_s, idle_s=self._cfg.idle_timeout_s
        )

    # ── entry point ─────────────────────────────────────────────────────────

    async def handle(self, call: GitCall, body: AsyncIterator[bytes]) -> GitReply:
        """Authorize and serve ``call``; ``body`` is the client's request body stream.

        Raises a :class:`BrokerError` for a request refused before any
        response is streamed (no binding, a fetch the rules deny, an
        unreadable push, an upstream failure); a push refused ref by ref is a
        :class:`GitReply` carrying report-status.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._cfg.transfer_timeout_s
        started_at = datetime.now(UTC)
        started_perf = time.perf_counter()
        target = call.target
        binding = await self._binding(call)
        if target.endpoint is GitEndpoint.INFO_REFS:
            action = GitAction.FETCH if target.service is GitService.UPLOAD_PACK else GitAction.PUSH
            await self._require(call, binding, action)
            auth = await self._authorize(call, binding)
            return await self._advertise(call, auth, deadline, started_at, started_perf)
        if target.service is GitService.UPLOAD_PACK:
            await self._require(call, binding, GitAction.FETCH)
            auth = await self._authorize(call, binding)
            return await self._upload_pack(call, auth, body, deadline, started_at, started_perf)
        return await self._receive_pack(call, binding, body, deadline, started_at, started_perf)

    async def record(self, reply: GitReply) -> None:
        """Persist the execution record of a finished reply (best effort)."""
        if reply.record is None:
            return
        outcome = reply.outcome
        status = (
            ExecutionStatus.FAILED
            if reply.status is ExecutionStatus.FAILED
            or outcome.error is not None
            or outcome.http_status >= 400
            else ExecutionStatus.COMPLETED
        )
        try:
            async with self._ctx.admin_db.transaction() as session:
                await persist_streaming_execution(
                    session,
                    execution_id=outcome.execution_id,
                    started_at=reply.started_at,
                    status=status,
                    http_status=outcome.http_status,
                    duration_ms=outcome.duration_ms,
                    error=outcome.error[:_MAX_RECORDED_ERROR] if outcome.error else None,
                    ctx_req=reply.record,
                    actor_id=reply.identity.sub,
                    actor_type=reply.identity.actor_type.value,
                    origin=reply.identity.origin.value,
                    security_config=self._ctx.config.security,
                )
        except Exception:
            logger.error(
                "git_execution_persist_failed", execution_id=outcome.execution_id, exc_info=True
            )

    # ── authorization ───────────────────────────────────────────────────────

    async def _binding(self, call: GitCall) -> _Binding:
        """The agent's binding for the git API and its selected credential (403/424 if none)."""
        derivation = await derive_credential_bindings(
            deriver=self._deriver,
            identity=call.identity,
            api=GITHUB_GIT_API,
            instance=call.instance,
            ctx=self._ctx,
            # No method or path: a denial must not suggest a path rule, which
            # never decides a git request.
            method="",
            path="",
            trace_id=call.trace_id,
        )
        allowed = [bc.credential_id for bc in derivation.credentials]
        rule_sets = {bc.credential_id: bc.rule_set_id for bc in derivation.credentials}
        selected = await CredentialService(self._ctx).select(
            api_vendor=GITHUB_GIT_API.vendor,
            api_name=GITHUB_GIT_API.name,
            api_version=GITHUB_GIT_API.version,
            identity=call.identity,
            credential_name=call.credential_name,
            allowed_credential_ids=allowed,
        )
        assert selected is not None  # the git API has a concrete vendor
        return _Binding(
            credential=selected,
            allowed_credential_ids=allowed,
            rule_set_id=rule_sets.get(selected.credential_id),
        )

    async def _evaluate(
        self, call: GitCall, binding: _Binding, items: Sequence[GitItem]
    ) -> GitEvaluation:
        return await self._rules.evaluate_git(
            agent_id=call.identity.sub,
            credential_id=binding.credential.credential_id,
            rule_set_id=binding.rule_set_id,
            items=items,
            # A profile-scoped credential's git API takes its git rules only.
            api_role=binding_api_role(binding.credential, GITHUB_GIT_API),
        )

    async def _require(self, call: GitCall, binding: _Binding, action: GitAction) -> None:
        """Refuse (403) a fetch or a push advertisement the binding's git rules do not allow."""
        target = call.target
        evaluation = await self._evaluate(
            call, binding, [GitItem(action, target.rule_owner, target.rule_repo)]
        )
        if evaluation.items[0].allowed:
            return
        verb = "fetching" if action is GitAction.FETCH else "pushing to"
        code = (
            DenialReason.NO_RULES_LOADED
            if evaluation.rules_loaded == 0
            else DenialReason.NO_RULE_MATCHED
        )
        await record_pbac_denial(
            self._ctx,
            identity=call.identity,
            api=GITHUB_GIT_API,
            reason=code,
            summary=f"git {action.value} of {target.full_name} denied by the binding's git rules",
            trace_id=call.trace_id,
            data={
                "operation_id": operation_for(target).id,
                "repository": target.full_name,
                "git_action": action.value,
            },
            mode="git",
        )
        raise ActionDeniedError(
            f"The binding's git rules do not allow {verb} {target.full_name} through Jentic.",
            type="action_denied",
            instance=call.instance,
            directive=git_action_denied_directive(action=action.value, repository=target.full_name),
        )

    async def _authorize(self, call: GitCall, binding: _Binding) -> _Auth:
        """Decrypt and inject the selected credential as git's Basic auth (424 if not a token)."""
        injection = await CredentialService(self._ctx).inject(
            api_vendor=GITHUB_GIT_API.vendor,
            api_name=GITHUB_GIT_API.name,
            api_version=GITHUB_GIT_API.version,
            identity=call.identity,
            credential_name=call.credential_name,
            allowed_credential_ids=binding.allowed_credential_ids,
            trace_id=call.trace_id,
            preresolved=binding.credential,
        )
        token = bearer_token(injection.headers)
        if token is None or injection.signing is not None:
            raise GitCredentialUnsupportedError(
                "The credential bound for github.com is not a token, which git needs.",
                instance=call.instance,
                directive=git_credential_unsupported_directive(),
            )
        return _Auth(
            git=git_basic_authorization(token),
            api=f"Bearer {token}",
            credential_id=injection.credential_id,
            credential_name=injection.credential_name,
        )

    # ── fetch ───────────────────────────────────────────────────────────────

    async def _advertise(
        self,
        call: GitCall,
        auth: _Auth,
        deadline: float,
        started_at: datetime,
        started_perf: float,
    ) -> GitReply:
        target = call.target
        response = await self._upstream.send(
            "GET",
            target.upstream_path,
            query=target.upstream_query,
            headers=self._upstream_headers(call, auth, accept="*/*"),
            body=None,
            timeouts=self._timeouts,
        )
        media_type = f"application/x-{target.service.value}-advertisement"
        await self._require_upstream_ok(response, media_type)
        outcome = self._outcome(response.status_code, started_perf)
        body = self._relay(
            response.iter_decoded(), response, deadline, outcome, advert=AdvertisementFilter()
        )
        return self._reply(call, auth, response, media_type, body, outcome, started_at)

    async def _upload_pack(
        self,
        call: GitCall,
        auth: _Auth,
        body: AsyncIterator[bytes],
        deadline: float,
        started_at: datetime,
        started_perf: float,
    ) -> GitReply:
        target = call.target
        headers = self._upstream_headers(
            call,
            auth,
            accept="application/x-git-upload-pack-result",
            content_type="application/x-git-upload-pack-request",
        )
        response = await self._upstream.send(
            "POST",
            target.upstream_path,
            query="",
            headers=headers,
            body=self._capped_body(body, deadline),
            timeouts=self._timeouts,
        )
        media_type = "application/x-git-upload-pack-result"
        await self._require_upstream_ok(response, media_type)
        outcome = self._outcome(response.status_code, started_perf)
        relayed = self._relay(response.iter_raw(), response, deadline, outcome)
        return self._reply(call, auth, response, media_type, relayed, outcome, started_at)

    # ── push ────────────────────────────────────────────────────────────────

    async def _receive_pack(
        self,
        call: GitCall,
        binding: _Binding,
        body: AsyncIterator[bytes],
        deadline: float,
        started_at: datetime,
        started_perf: float,
    ) -> GitReply:
        spool = await self._spool(body, deadline)
        closer = _closer(spool.file)
        try:
            if not spool.too_large and is_flush_only(spool.head):
                closer()
                return self._probe_reply(call, started_at, started_perf)
            inspection = await self._inspect(call, spool)
            denials = await self._decide(call, binding, spool, inspection)
            auth: _Auth | None = None
            if denials is None:
                # Only an all-allowed push needs the credential before it is
                # forwarded: the rules were checked without decrypting it.
                auth = await self._authorize(call, binding)
                denials = await self._check_ancestry(call, inspection, auth)
            if denials:
                closer()
                return await self._refuse(
                    call, binding, inspection, denials, started_at, started_perf
                )
            assert auth is not None
            return await self._forward_push(
                call, auth, spool, closer, deadline, started_at, started_perf
            )
        except BaseException:
            closer()
            raise

    async def _spool(self, body: AsyncIterator[bytes], deadline: float) -> _Spool:
        """Read the push body into a bounded temporary file (memory first, then disk)."""
        limit = self._cfg.max_push_bytes
        in_memory = self._cfg.spool_memory_bytes
        spool = _Spool(
            file=cast(IO[bytes], tempfile.SpooledTemporaryFile(max_size=in_memory))  # noqa: SIM115
        )
        async for chunk in self._within(body, deadline, what="the push body"):
            if spool.too_large:
                # Drained, not kept: the client is still sending; it gets the
                # refusal in report-status once its body ends.
                continue
            if spool.size + len(chunk) > limit:
                # Keep the part under the limit: the ref commands at its start
                # still name the refs the refusal is reported for.
                spool.too_large = True
                chunk = chunk[: limit - spool.size]
            if len(spool.head) < 4:
                spool.head += chunk[: 4 - len(spool.head)]
            if spool.size + len(chunk) > in_memory:
                await asyncio.to_thread(spool.file.write, chunk)
            else:
                spool.file.write(chunk)
            spool.size += len(chunk)
        if spool.size > 4:
            spool.head = b""
        return spool

    async def _inspect(self, call: GitCall, spool: _Spool) -> _Inspection:
        try:
            return await asyncio.to_thread(
                _inspect_push,
                spool.file,
                content_encoding=(call.content_encoding or "").strip().lower() or None,
                cfg=self._cfg,
                skip_pack=spool.too_large,
            )
        except GitProtocolError as exc:
            raise GitRequestRejectedError(
                f"Jentic could not read this push: {exc}.",
                instance=call.instance,
                directive=git_request_rejected_directive(),
            ) from exc

    async def _decide(
        self, call: GitCall, binding: _Binding, spool: _Spool, inspection: _Inspection
    ) -> dict[str, _Denial] | None:
        """The refs the push limits and the rules refuse; ``None`` when none is refused.

        Branch updates that still need the force-push check are left on
        ``inspection.refs``.
        """
        head = inspection.head
        if spool.too_large:
            reason = push_too_large_reason(self._cfg.max_push_bytes)
            return {c.ref: _Denial(reason, DenialReason.GIT_PUSH_REJECTED) for c in head.commands}
        if inspection.pack_error is not None:
            reason = unreadable_pack_reason(inspection.pack_error)
            return {c.ref: _Denial(reason, DenialReason.GIT_PUSH_REJECTED) for c in head.commands}
        target = call.target
        denials: dict[str, _Denial] = {}
        plans: list[RefPlan] = []
        for command in head.commands:
            hard = ref_hard_deny_reason(command.ref)
            if hard is not None:
                denials[command.ref] = _Denial(hard, DenialReason.GIT_REF_HARD_DENIED)
                continue
            plans.append(plan_command(command, owner=target.rule_owner, repo=target.rule_repo))
        items = [p.item for p in plans] + [p.force_item for p in plans if p.force_item]
        evaluation = await self._evaluate(call, binding, items) if items else None
        allowed = (
            {(e.item.action, e.item.ref): e.allowed for e in evaluation.items}
            if evaluation is not None
            else {}
        )
        no_rules = evaluation is not None and evaluation.rules_loaded == 0
        for plan in plans:
            command = plan.command
            if not allowed.get((plan.item.action, plan.item.ref), False):
                code = (
                    DenialReason.NO_RULES_LOADED
                    if no_rules
                    else DenialReason.GIT_FORCE_DENIED
                    if plan.item.action is GitAction.FORCE
                    else DenialReason.NO_RULE_MATCHED
                )
                denials[command.ref] = _Denial(rules_denied_reason(command), code, plan.item.action)
            elif plan.force_item is not None and not allowed.get(
                (GitAction.FORCE, command.ref), False
            ):
                inspection.refs.append(command.ref)
        return denials or None

    async def _check_ancestry(
        self, call: GitCall, inspection: _Inspection, auth: _Auth
    ) -> dict[str, _Denial] | None:
        """The force-push check for the branch updates that need it (see ``git_push``)."""
        if not inspection.refs:
            return None
        index = inspection.index or PackIndex()
        budget = _Budget(left=self._cfg.max_compare_requests)
        denials: dict[str, _Denial] = {}
        by_ref = {c.ref: c for c in inspection.head.commands}
        for ref in inspection.refs:
            command = by_ref[ref]
            denial = await self._ancestry(call, index, command.old, command.new, auth, budget)
            if denial is not None:
                if denial.code is DenialReason.GIT_FORCE_DENIED:
                    denial = _Denial(non_fast_forward_reason(command), denial.code, GitAction.FORCE)
                denials[ref] = denial
        return denials or None

    async def _ancestry(
        self,
        call: GitCall,
        index: PackIndex,
        old: str,
        new: str,
        auth: _Auth,
        budget: _Budget,
    ) -> _Denial | None:
        """``None`` when ``old`` is shown to be an ancestor of ``new``, else why not."""
        walk = walk_to_boundaries(index, new, old)
        if walk.reached_old:
            return None
        missing: str | None = None
        unanswered = False
        target = call.target
        for boundary in walk.boundaries:
            if budget.left <= 0:
                return _Denial(
                    too_many_checks_reason(self._cfg.max_compare_requests),
                    DenialReason.GIT_ANCESTRY_UNVERIFIED,
                )
            budget.left -= 1
            result = await self._upstream.compare(
                target.owner,
                target.repo,
                old,
                boundary,
                authorization=auth.api,
                timeout_s=self._cfg.compare_timeout_s,
                max_bytes=_COMPARE_MAX_BYTES,
            )
            if result.status in ("ahead", "identical"):
                return None
            if result.status is None:
                if result.http_status == 404:
                    missing = missing or boundary
                else:
                    unanswered = True
        if missing is not None:
            if index.unresolved:
                return _Denial(thin_pack_reason(), DenialReason.GIT_ANCESTRY_UNVERIFIED)
            return _Denial(unknown_commit_reason(missing), DenialReason.GIT_ANCESTRY_UNVERIFIED)
        if unanswered:
            return _Denial(unverified_reason(), DenialReason.GIT_ANCESTRY_UNVERIFIED)
        return _Denial("", DenialReason.GIT_FORCE_DENIED, GitAction.FORCE)

    async def _refuse(
        self,
        call: GitCall,
        binding: _Binding,
        inspection: _Inspection,
        denials: Mapping[str, _Denial],
        started_at: datetime,
        started_perf: float,
    ) -> GitReply:
        """Answer a refused push with report-status; nothing reaches GitHub."""
        head = inspection.head
        target = call.target
        for position, (ref, denial) in enumerate(denials.items()):
            if position >= _MAX_DENIAL_EVENTS:
                break
            await record_pbac_denial(
                self._ctx,
                identity=call.identity,
                api=GITHUB_GIT_API,
                reason=denial.code,
                summary=f"git push to {target.full_name} {ref} denied: {denial.reason}"[:300],
                trace_id=call.trace_id,
                data={
                    "operation_id": RECEIVE_PACK_OPERATION.id,
                    "repository": target.full_name,
                    "ref": ref,
                    "git_action": (denial.action or GitAction.PUSH).value,
                    "detail": denial.reason,
                },
                mode="git",
            )
        first_ref, first = next(iter(denials.items()))
        progress = [
            f"Jentic refused this push: {first_ref}: {first.reason}",
            "Nothing in the push was applied. Ask your operator if the task needs these refs.",
        ]
        report = denied_report_status(
            head,
            {ref: denial.reason for ref, denial in denials.items()},
            others_reason=NOT_APPLIED_REASON,
            progress=progress,
        )
        logger.info(
            "git_push_refused",
            repository=target.full_name,
            refused_refs=len(denials),
            ref_updates=len(head.commands),
        )
        outcome = StreamingOutcome(
            execution_id=mint_execution_id(),
            http_status=200,
            started_at_perf=started_perf,
            duration_ms=int((time.perf_counter() - started_perf) * 1000),
            error=f"Refused by Jentic: {first_ref}: {first.reason}",
            bytes_transferred=len(report),
        )

        async def _body() -> AsyncIterator[bytes]:
            yield report

        return GitReply(
            status_code=200,
            media_type="application/x-git-receive-pack-result",
            body=_body(),
            headers=self._reply_headers(outcome.execution_id),
            outcome=outcome,
            started_at=started_at,
            identity=call.identity,
            record=self._record_context(
                call, binding.credential.credential_id, binding.credential.name
            ),
            status=ExecutionStatus.FAILED,
        )

    async def _forward_push(
        self,
        call: GitCall,
        auth: _Auth,
        spool: _Spool,
        closer: Callable[[], None],
        deadline: float,
        started_at: datetime,
        started_perf: float,
    ) -> GitReply:
        """Send the client's original push body upstream once and relay the report."""
        target = call.target
        headers = self._upstream_headers(
            call,
            auth,
            accept="application/x-git-receive-pack-result",
            content_type="application/x-git-receive-pack-request",
        )
        response = await self._upstream.send(
            "POST",
            target.upstream_path,
            query="",
            headers=headers,
            body=_read_spool(spool.file),
            timeouts=self._timeouts,
        )
        media_type = "application/x-git-receive-pack-result"
        await self._require_upstream_ok(response, media_type)
        outcome = self._outcome(response.status_code, started_perf)
        relayed = self._relay(response.iter_raw(), response, deadline, outcome, cleanup=closer)
        return self._reply(call, auth, response, media_type, relayed, outcome, started_at)

    def _probe_reply(self, call: GitCall, started_at: datetime, started_perf: float) -> GitReply:
        """Answer git's flush-only authentication probe locally (nothing is recorded)."""

        async def _empty() -> AsyncIterator[bytes]:
            return
            yield b""  # pragma: no cover - makes this an async generator

        return GitReply(
            status_code=200,
            media_type="application/x-git-receive-pack-result",
            body=_empty(),
            headers={},
            outcome=self._outcome(200, started_perf),
            started_at=started_at,
            identity=call.identity,
            record=None,
        )

    # ── streaming helpers ───────────────────────────────────────────────────

    def _upstream_headers(
        self,
        call: GitCall,
        auth: _Auth,
        *,
        accept: str,
        content_type: str | None = None,
    ) -> dict[str, str]:
        """The headers sent to github.com: git's own, with the injected credential.

        ``Git-Protocol`` is not forwarded, so github.com answers protocol v0,
        whose ref advertisement the pipeline can filter; any client header
        other than these never reaches the upstream.
        """
        user_agent = call.user_agent or ""
        headers = {
            "Authorization": auth.git,
            "User-Agent": user_agent if user_agent.startswith("git/") else "git/jentic",
            "Accept": accept,
            "Accept-Encoding": "identity",
            "Pragma": "no-cache",
        }
        if content_type is not None:
            headers["Content-Type"] = content_type
            if call.content_encoding:
                headers["Content-Encoding"] = call.content_encoding
        return headers

    async def _require_upstream_ok(self, response: GitUpstreamResponse, media_type: str) -> None:
        """Raise (closing the response) unless github.com answered a smart-HTTP ``media_type``."""
        status = response.status_code
        content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
        if status == 200 and content_type == media_type:
            return
        await response.aclose()
        raise _upstream_failure(status, content_type, response.headers.get("location"))

    def _outcome(self, http_status: int, started_perf: float) -> StreamingOutcome:
        return StreamingOutcome(
            execution_id=mint_execution_id(), http_status=http_status, started_at_perf=started_perf
        )

    def _reply_headers(self, execution_id: str) -> dict[str, str]:
        return {"Cache-Control": "no-cache", JenticHeader.EXECUTION_ID.value: execution_id}

    def _reply(
        self,
        call: GitCall,
        auth: _Auth,
        response: GitUpstreamResponse,
        media_type: str,
        body: AsyncIterator[bytes],
        outcome: StreamingOutcome,
        started_at: datetime,
    ) -> GitReply:
        headers = self._reply_headers(outcome.execution_id)
        encoding = response.headers.get("content-encoding")
        if encoding and call.target.endpoint is GitEndpoint.RPC:
            headers["Content-Encoding"] = encoding
        return GitReply(
            status_code=200,
            media_type=media_type,
            body=body,
            headers=headers,
            outcome=outcome,
            started_at=started_at,
            identity=call.identity,
            record=self._record_context(call, auth.credential_id, auth.credential_name),
        )

    def _record_context(
        self, call: GitCall, credential_id: str | None, credential_name: str | None
    ) -> ExecuteRequestContext:
        target = call.target
        operation = operation_for(target)
        url = f"https://{GIT_UPSTREAM_HOST}{target.upstream_path}"
        if target.upstream_query:
            url = f"{url}?{target.upstream_query}"
        return ExecuteRequestContext(
            upstream_url=url,
            method=operation.method or "POST",
            trace_id=call.trace_id,
            operation=operation,
            api_vendor=GITHUB_GIT_API.vendor,
            api_name=GITHUB_GIT_API.name,
            api_version=GITHUB_GIT_API.version,
            credential_id=credential_id,
            credential_name=credential_name,
        )

    async def _within(
        self, chunks: AsyncIterator[bytes], deadline: float, *, what: str
    ) -> AsyncIterator[bytes]:
        """``chunks``, each within the idle timeout and the request's overall deadline."""
        loop = asyncio.get_running_loop()
        iterator = aiter(chunks)
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise UpstreamTimeoutError(
                    f"The git transfer took longer than {self._cfg.transfer_timeout_s:g} seconds.",
                    type="git_transfer_timeout",
                )
            try:
                chunk = await asyncio.wait_for(
                    anext(iterator), timeout=min(remaining, self._cfg.idle_timeout_s)
                )
            except StopAsyncIteration:
                return
            except TimeoutError as exc:
                raise UpstreamTimeoutError(
                    f"{what.capitalize()} stalled for longer than "
                    f"{self._cfg.idle_timeout_s:g} seconds.",
                    type="git_transfer_timeout",
                ) from exc
            yield chunk

    async def _capped_body(
        self, body: AsyncIterator[bytes], deadline: float
    ) -> AsyncIterator[bytes]:
        """The client's request body, refused (413) past ``max_push_bytes``."""
        total = 0
        async for chunk in self._within(body, deadline, what="the request body"):
            total += len(chunk)
            if total > self._cfg.max_push_bytes:
                raise PayloadTooLargeError(
                    f"The git request body is larger than Jentic's "
                    f"{self._cfg.max_push_bytes}-byte limit.",
                    type="payload_too_large",
                )
            yield chunk

    async def _relay(
        self,
        chunks: AsyncIterator[bytes],
        response: GitUpstreamResponse,
        deadline: float,
        outcome: StreamingOutcome,
        *,
        advert: AdvertisementFilter | None = None,
        cleanup: Callable[[], None] | None = None,
    ) -> AsyncIterator[bytes]:
        """Stream the upstream body to the client, recording how it ended on ``outcome``."""
        try:
            async for chunk in self._within(chunks, deadline, what="the github.com response"):
                outcome.bytes_transferred += len(chunk)
                out = advert.feed(chunk) if advert is not None else chunk
                if out:
                    yield out
            if advert is not None:
                tail = advert.close()
                if tail:
                    yield tail
        except GitProtocolError:
            outcome.error = "github.com sent an unreadable ref advertisement"
            raise
        except BrokerError as exc:
            outcome.error = exc.detail
            raise
        except (asyncio.CancelledError, GeneratorExit):
            outcome.error = outcome.error or "client_disconnect"
            raise
        finally:
            outcome.duration_ms = int((time.perf_counter() - outcome.started_at_perf) * 1000)
            await response.aclose()
            if cleanup is not None:
                cleanup()


def _closer(file: IO[bytes]) -> Callable[[], None]:
    """An idempotent close for the push spool."""

    def _close() -> None:
        if not file.closed:
            file.close()

    return _close


async def _read_spool(file: IO[bytes]) -> AsyncIterator[bytes]:
    """The spooled push body from its start, read off the event loop."""
    await asyncio.to_thread(file.seek, 0)
    while True:
        chunk = await asyncio.to_thread(file.read, _CHUNK)
        if not chunk:
            return
        yield chunk


def _upstream_failure(status: int, content_type: str, location: str | None) -> BrokerError:
    """The error for a github.com answer that is not the smart-HTTP response git needs."""
    if 300 <= status < 400:
        hint = f" to {location}" if location else ""
        return UpstreamTransportError(
            f"github.com redirected the git request{hint}; Jentic does not follow git "
            "redirects. If the repository was renamed or moved, update the remote URL.",
            type="git_upstream_redirect",
        )
    if status in (401, 403):
        return ActionDeniedError(
            f"github.com refused the stored GitHub credential ({status}): it may have "
            "expired or lack access to this repository.",
            type="git_upstream_denied",
        )
    if status == 404:
        return OperationNotFoundError(
            "github.com: repository not found, or the stored GitHub credential cannot see it.",
            type="git_repository_not_found",
        )
    if status == 200:
        return UpstreamTransportError(
            f"github.com did not answer as a smart-HTTP git server ({content_type or 'no type'}).",
            type="upstream_transport_error",
        )
    return UpstreamTransportError(
        f"github.com answered the git request with HTTP {status}.",
        type="upstream_transport_error",
    )


__all__ = ["GitCall", "GitPipeline", "GitReply"]
