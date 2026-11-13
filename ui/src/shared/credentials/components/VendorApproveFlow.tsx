import type { ReactNode } from 'react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Button, ConfirmDialog, ErrorAlert, Skeleton, toast } from '@/shared/ui';
import {
	connectRequestsKey,
	useAgentsForPicker,
	useAmendConnectSession,
	useCancelConnectSession,
	useConfirmConnectSession,
	useConnectSession,
	usePollConnectSessionStatus,
	useRejectConnectSession,
} from '@/shared/credentials/api/vendors-hooks';
import {
	cancelConnectSession,
	cancelConnectSessionOnUnload,
} from '@/shared/credentials/api/vendors-client';
import { useProviders } from '@/shared/credentials/api';
import {
	ApiTargetDetails,
	ConfirmBlockedNotice,
	ConnectMethodField,
	OwnOAuthClientStep,
	SecretEntryStep,
	buildMethodOptions,
	parseMethodKey,
	secretStepSubtitle,
	type EnteredOAuthClient,
	type EnteredSecret,
} from '@/shared/credentials/components/ConnectReviewParts';
import {
	confirmBlockedReason,
	describeConfirmError,
	isAwaitingApp,
	schemeSummary,
	secretKindOf,
	secretNoun,
} from '@/shared/credentials/lib/connectReview';
import { sameRules } from '@/shared/credentials/lib/connectEdit';
import { isHttpsVendorUrl, openVendorUrl } from '@/shared/credentials/lib/safe-navigation';
import { useSharedAppSurface } from '@/shared/credentials/lib/sharedAppSurface';
import type {
	ConfirmResponse,
	ConfirmSessionBody,
	ConfirmSessionResponse,
	PermissionRule,
	ReviewEdit,
	ReviewScope,
	ReviewSession,
	VendorScopeCatalog,
} from '@/shared/credentials/api/vendors-types';
import { EditApproveFlow } from './EditApproveFlow';
import {
	AgentRequestCard,
	AwaitingStep,
	PreviouslyRejected,
	RulesStep,
	ScopeChooseField,
	TerminalStep,
	VendorHeader,
} from './VendorConnectSteps';
import type { Phase, PostConnectInfo, VendorDisplay } from './vendorFlowTypes';

/**
 * What to tell a human whose approval link did not load. A 403 is the
 * backend's uniform "missing, not your token, or not your agent" answer, so
 * it reads as one message; anything else surfaces the server's own detail.
 */
function approvalLoadError(error: unknown): string {
	if ((error as { status?: number | null } | null)?.status === 403) {
		return "This request is no longer open, or it isn't yours to approve.";
	}
	return (error as Error | null)?.message ?? 'The approval link is no longer valid.';
}

/** The approve dialog's steps; `secret` / `own_client` collect what `:confirm` sends last. */
type ApprovePhase = Phase | 'secret' | 'own_client';

/** A step's inline error after a failed `:confirm`, shown on the step it names. */
interface StepError {
	phase: ApprovePhase;
	message: string;
}

/** How the session ended inside the dialog, when `:confirm` (not `/status`) decided it. */
type LocalOutcome =
	{ status: 'connected'; credentialId: string } | { status: 'failed'; message: string };

export function VendorApproveFlow({
	sessionId,
	pollToken,
	renderPostConnect,
	onBack,
	onDone,
}: {
	sessionId: string;
	/** Absent for the owner / org-admin path (the approval URL carries none). */
	pollToken?: string;
	renderPostConnect?: (info: PostConnectInfo) => ReactNode;
	onBack: () => void;
	onDone: () => void;
}) {
	// The review read is gated server-side by the poll_token or, without
	// one, by the caller owning the target agent (or being org:admin). A
	// missing session, a token mismatch and a caller who is neither all come
	// back 403 (no enumeration oracle), so the error branch below treats any
	// 403 as "this request isn't open to you".
	const sessionQuery = useConnectSession(sessionId, pollToken);
	const agents = useAgentsForPicker();
	const providers = useProviders();
	const sharedAppSurface = useSharedAppSurface();
	const queryClient = useQueryClient();

	const [selectedScopes, setSelectedScopes] = useState<Set<string> | null>(null);
	const [phase, setPhase] = useState<ApprovePhase>('configure');
	const [rules, setRules] = useState<PermissionRule[] | null>(null);
	const [challenge, setChallenge] = useState<ConfirmResponse | null>(null);
	const [methodValue, setMethodValue] = useState<string>('primary');
	const [stepError, setStepError] = useState<StepError | null>(null);
	const [outcome, setOutcome] = useState<LocalOutcome | null>(null);
	const [rejectOpen, setRejectOpen] = useState(false);

	const confirmMutation = useConfirmConnectSession(sessionId, pollToken);
	const amendMutation = useAmendConnectSession(sessionId);
	const cancelMutation = useCancelConnectSession();
	const rejectMutation = useRejectConnectSession();
	const phaseRef = useRef<ApprovePhase>('configure');

	const session: ReviewSession | undefined = sessionQuery.data;
	const scopes: ReviewScope[] = useMemo(() => session?.scopes ?? [], [session]);
	const secretKind = session ? secretKindOf(session.resolved_flow) : null;
	const awaitingApp = session ? isAwaitingApp(session) : false;
	const candidates = useMemo(() => session?.existing_credentials ?? [], [session]);
	const requestedScopes = useMemo(() => session?.requested_scopes ?? [], [session]);
	// The shared-app option needs the host's surface and an app-less OAuth API.
	const offerSharedApp = awaitingApp && sharedAppSurface != null;
	const method = parseMethodKey(methodValue);

	const goToPhase = (next: ApprovePhase): void => {
		phaseRef.current = next;
		setPhase(next);
	};

	// Cancel-on-unmount mirror of the self-flow: if the user closes the
	// approval dialog while the vendor sign-in is under way, the pending
	// credential + session must be cleaned up server-side. That is a
	// cancel (`cancelled`), never a reject. Closing it before confirming
	// sends nothing: the request stays open for later. See
	// ``VendorSelfConnectFlow`` for why we fire the raw client instead of
	// the mutation.
	useEffect(() => {
		return () => {
			if (phaseRef.current !== 'awaiting') return;
			void cancelConnectSession(sessionId, pollToken).catch(() => {});
		};
	}, [sessionId, pollToken]);

	// Tab-close variant — see the sibling ``VendorSelfConnectFlow`` effect
	// for the rationale (fetch aborts on unload; a keepalive request
	// delivers, and carries the bearer the token-less path needs).
	useEffect(() => {
		const onBeforeUnload = (): void => {
			if (phaseRef.current !== 'awaiting') return;
			cancelConnectSessionOnUnload(sessionId, pollToken);
		};
		window.addEventListener('beforeunload', onBeforeUnload);
		return () => window.removeEventListener('beforeunload', onBeforeUnload);
	}, [sessionId, pollToken]);

	// "Not now": close and leave the request open — no server call, so the
	// agent keeps waiting and the request stays in the inbox.
	const handleNotNow = (): void => {
		onBack();
	};

	// Cancel while the vendor sign-in is under way: the session already left
	// `created`, so it is cancelled server-side (as an unmount would).
	const handleCancelSignIn = (): void => {
		cancelMutation.mutate({ sessionId, pollToken });
		phaseRef.current = 'terminal';
		onBack();
	};

	const handleReject = async (): Promise<void> => {
		try {
			await rejectMutation.mutateAsync({ sessionId });
			setRejectOpen(false);
			phaseRef.current = 'terminal';
			toast({
				title: 'Request rejected',
				description: "The agent is told it was rejected and won't ask again for a while.",
				variant: 'success',
			});
			onDone();
		} catch {
			// surfaced in the confirm dialog below.
		}
	};

	// Seed selection from what the agent + defaults pre-selected on the session.
	// The human can still tweak it; once they've clicked anything the initial
	// seed no longer overrides their intent.
	useEffect(() => {
		if (selectedScopes !== null) return;
		if (!session) return;
		setSelectedScopes(
			new Set(scopes.filter((s) => s.default || s.requested).map((s) => s.name)),
		);
	}, [session, scopes, selectedScopes]);

	const methodOptions = useMemo(() => {
		if (!session) return [];
		const display = session.vendor_display_name;
		const primary = awaitingApp
			? {
					label: 'Bring your own OAuth client',
					description: `Use an OAuth app you registered with ${display}: its client ID, secret and endpoints.`,
				}
			: secretKind
				? {
						label: `Enter a new ${secretNoun(secretKind)}`,
						description: `Connect a new credential for ${display}, bound only to this agent.`,
					}
				: {
						label: `Sign in to ${display}`,
						description: 'Connect a new credential with the scopes you pick below.',
					};
		return buildMethodOptions({
			primaryLabel: primary.label,
			primaryDescription: primary.description,
			sharedApp: offerSharedApp,
			candidates,
			requestedScopes,
		});
	}, [session, awaitingApp, secretKind, offerSharedApp, candidates, requestedScopes]);

	// A reload can drop the picked option (the credential went away, or the
	// session left `awaiting_app`): fall back to the session's own flow.
	useEffect(() => {
		if (methodOptions.length === 0) return;
		const picked = methodOptions.find((o) => o.value === methodValue);
		if (!picked || picked.disabled) setMethodValue('primary');
	}, [methodOptions, methodValue]);

	const polling = usePollConnectSessionStatus(sessionId, pollToken, {
		enabled: phase === 'awaiting',
		// RFC 8628 §3.5: honour the vendor's ``interval`` when supplied,
		// fall back to the spec-mandated 5s (never the hook's default —
		// that's for our own ``/status`` reads, not vendor cadence).
		intervalMs:
			challenge?.kind === 'device_authorization'
				? (challenge.poll_interval_seconds ?? 5) * 1000
				: undefined,
	});

	useEffect(() => {
		if (phase !== 'awaiting') return;
		// See ``VendorSelfConnectFlow`` for the 403-as-terminal rationale
		// (backend cascades the session on unhappy terminal, and the
		// service raises ``InvalidPollTokenError`` for missing sessions
		// to close the enumeration oracle — but this caller already
		// passed the same gate on the review read and ``:confirm``, with
		// or without a poll_token, so a 403 here is unambiguously
		// "session gone → terminal").
		const err = polling.error as { status?: number } | undefined;
		if (err?.status === 403) {
			goToPhase('terminal');
			// The request is no longer open: drop it from the waiting signals.
			void queryClient.invalidateQueries({ queryKey: connectRequestsKey });
			return;
		}
		if (!polling.data) return;
		const status = polling.data.status;
		if (status === 'connected' || status === 'failed' || status === 'expired') {
			goToPhase('terminal');
			void queryClient.invalidateQueries({ queryKey: connectRequestsKey });
			if (status === 'connected') {
				toast({
					title: `Connected to ${session?.vendor_display_name ?? 'the integration'}`,
					description: polling.data.connected_as
						? `Signed in as ${polling.data.connected_as}.`
						: undefined,
					variant: 'success',
				});
				void queryClient.invalidateQueries({ queryKey: ['credentials'] });
			}
		}
	}, [phase, polling.data, polling.error, session, queryClient]);

	const toggleScope = (name: string) => {
		setSelectedScopes((prev) => {
			const next = new Set(prev ?? []);
			if (next.has(name)) next.delete(name);
			else next.add(name);
			return next;
		});
	};

	// Continue on the review page — no backend call, just move to rules.
	// Rules seed from the agent's ``requested_permission_rules`` when the
	// agent supplied any (for an API target, the minimal rule that allows
	// the denied operation); otherwise the list starts empty.
	const goToRules = (): void => {
		setStepError(null);
		setRules((prev) => {
			if (prev !== null) return prev;
			return session?.requested_permission_rules ?? [];
		});
		goToPhase('rules');
	};

	/** The review the approver saw, echoed so the server can refuse a stale one. */
	const reviewed = (
		finalRules: PermissionRule[],
		review: ReviewSession | undefined = session,
	) => ({
		permission_rules: finalRules,
		expected_agent_id: review?.agent?.agent_id ?? null,
		digest: review?.digest ?? '',
		revision_id: review?.revision?.id ?? null,
	});

	/**
	 * The approver's rules, saved as a revision of the request (recorded as
	 * theirs) when they differ from what it asks. Resolves to the review to
	 * confirm against, re-read after a save; null when the save failed.
	 */
	const saveRules = async (finalRules: PermissionRule[]): Promise<ReviewSession | null> => {
		if (!session) return null;
		if (sameRules(finalRules, session.requested_permission_rules ?? [])) return session;
		try {
			await amendMutation.mutateAsync({ requested_permission_rules: finalRules });
			const fresh = await sessionQuery.refetch();
			return fresh.data ?? null;
		} catch (err) {
			setStepError({
				phase: 'rules',
				message: err instanceof Error ? err.message : 'Your rules couldn’t be saved.',
			});
			return null;
		}
	};

	const afterFailedConfirm = (err: unknown, from: ApprovePhase): void => {
		const copy = describeConfirmError(err);
		if (copy.action === 'ended') {
			setOutcome({ status: 'failed', message: copy.message });
			goToPhase('terminal');
			void queryClient.invalidateQueries({ queryKey: connectRequestsKey });
			return;
		}
		if (copy.action === 'reload') {
			// What was reviewed changed: re-read it and send the approver back
			// to the review, with the reason on top.
			void sessionQuery.refetch();
			setStepError({ phase: 'configure', message: copy.message });
			goToPhase('configure');
			return;
		}
		if (copy.action === 'rules') {
			setStepError({ phase: 'rules', message: copy.message });
			goToPhase('rules');
			return;
		}
		setStepError({ phase: from, message: copy.message });
	};

	/**
	 * Send ``:confirm``. A body carrying a secret is cleared from the mutation
	 * state as soon as the call settles, so it is not kept as ``variables``.
	 */
	const sendConfirm = async (
		body: ConfirmSessionBody,
		from: ApprovePhase,
		carriesSecret: boolean,
	): Promise<ConfirmSessionResponse | null> => {
		setStepError(null);
		try {
			return await confirmMutation.mutateAsync(body);
		} catch (err) {
			afterFailedConfirm(err, from);
			return null;
		} finally {
			if (carriesSecret) confirmMutation.reset();
		}
	};

	const finishConnected = (credentialId: string): void => {
		setOutcome({ status: 'connected', credentialId });
		goToPhase('terminal');
		void queryClient.invalidateQueries({ queryKey: connectRequestsKey });
		void queryClient.invalidateQueries({ queryKey: ['credentials'] });
		toast({
			title: `Connected to ${session?.vendor_display_name ?? 'the integration'}`,
			variant: 'success',
		});
	};

	const startVendorChallenge = (result: ConfirmResponse): void => {
		setChallenge(result);
		goToPhase('awaiting');
		// Vendor-supplied URL — https-only guard mirrors ``startFlow``.
		if (result.kind === 'authorization_code' && isHttpsVendorUrl(result.authorize_url)) {
			openVendorUrl(result.authorize_url, '_blank', 'noopener,noreferrer');
		}
	};

	const handleResult = (result: ConfirmSessionResponse | null): void => {
		if (!result) return;
		if (result.kind === 'connected') {
			finishConnected(result.credential_id);
			return;
		}
		startVendorChallenge(result);
	};

	// Continue on the rules page. The session's own OAuth flow and an
	// existing credential confirm here; a typed secret or an own OAuth
	// client is collected on the next step, last, right before it is sent.
	const afterRules = async (finalRules: PermissionRule[]): Promise<void> => {
		setStepError(null);
		setRules(finalRules);
		const review = await saveRules(finalRules);
		if (!review) return;
		if (method.kind === 'existing') {
			handleResult(
				await sendConfirm(
					{
						kind: 'existing_credential',
						credential_id: method.credentialId,
						...reviewed(finalRules, review),
					},
					'rules',
					false,
				),
			);
			return;
		}
		if (secretKind) {
			goToPhase('secret');
			return;
		}
		if (awaitingApp) {
			goToPhase('own_client');
			return;
		}
		handleResult(
			await sendConfirm(
				{
					confirmed_scopes: Array.from(selectedScopes ?? []),
					...reviewed(finalRules, review),
				},
				'rules',
				false,
			),
		);
	};

	const submitSecret = async (secret: EnteredSecret): Promise<void> => {
		handleResult(await sendConfirm({ ...secret, ...reviewed(rules ?? []) }, 'secret', true));
	};

	const submitOwnClient = async (client: EnteredOAuthClient): Promise<void> => {
		handleResult(
			await sendConfirm(
				{
					kind: 'own_oauth_client',
					client_id: client.clientId,
					client_secret: client.clientSecret,
					authorize_url: client.authorizeUrl,
					token_url: client.tokenUrl,
					confirmed_scopes: Array.from(selectedScopes ?? []),
					...reviewed(rules ?? []),
				},
				'own_client',
				true,
			),
		);
	};

	const errorFor = (step: ApprovePhase): string | null =>
		stepError?.phase === step ? stepError.message : null;

	if (session?.edit) {
		return (
			<EditApproveFlow
				// Mount-once per session: its state (the revision shown, whether
				// the open read was stale, whether a rebase was asked for) is
				// about one session, so a host swapping the id without an
				// unmount in between must not carry the last one's over.
				key={sessionId}
				sessionId={sessionId}
				pollToken={pollToken}
				session={session as ReviewSession & { edit: ReviewEdit }}
				onReload={(): void => void sessionQuery.refetch()}
				onBack={onBack}
				onDone={onDone}
			/>
		);
	}
	if (sessionQuery.isLoading) {
		return (
			<div className="space-y-4">
				<Skeleton className="h-12 w-full" />
				<Skeleton className="h-24 w-full" />
			</div>
		);
	}
	if ((sessionQuery.error || !session) && phase !== 'terminal') {
		return (
			<div className="space-y-4">
				<ErrorAlert message={approvalLoadError(sessionQuery.error)} />
				<div className="bg-surface-sheet-foot border-hairline-field sticky -bottom-4 z-10 -mx-5 mt-auto -mb-4 flex items-center justify-end border-t px-5 py-3.5">
					<Button type="button" variant="ghost" size="sm" onClick={onDone}>
						Close
					</Button>
				</div>
			</div>
		);
	}

	const display: VendorDisplay = {
		displayName: session?.vendor_display_name ?? 'the integration',
		iconKey: session?.vendor_key ?? '',
	};
	const boundAgentId = session?.agent?.agent_id ?? session?.requested_by_actor_id ?? null;

	if (phase === 'terminal') {
		if (outcome) {
			return (
				<TerminalStep
					display={display}
					status={outcome.status}
					connectedAs={null}
					errorCode={null}
					failureMessage={outcome.status === 'failed' ? outcome.message : undefined}
					credentialId={outcome.status === 'connected' ? outcome.credentialId : null}
					renderPostConnect={renderPostConnect}
					boundAgentId={boundAgentId}
					onDone={onDone}
				></TerminalStep>
			);
		}
		return (
			<TerminalStep
				display={display}
				status={polling.data?.status ?? 'failed'}
				connectedAs={polling.data?.connected_as ?? null}
				errorCode={polling.data?.error_code ?? null}
				credentialId={polling.data?.credential_id ?? null}
				renderPostConnect={renderPostConnect}
				boundAgentId={boundAgentId}
				onDone={onDone}
			/>
		);
	}

	if (!session) return null;

	if (phase === 'awaiting' && challenge) {
		return (
			<AwaitingStep
				display={display}
				challenge={challenge}
				status={polling.data?.status ?? 'pending'}
				onCancel={handleCancelSignIn}
			/>
		);
	}

	const apiTarget = session.target_kind === 'api';

	if (phase === 'rules') {
		return (
			<RulesStep
				display={display}
				requestedRules={session.requested_permission_rules ?? []}
				currentRules={rules ?? []}
				onChange={setRules}
				onBack={(): void => goToPhase('configure')}
				onContinue={(finalRules: PermissionRule[]) => void afterRules(finalRules)}
				submitting={confirmMutation.isPending || amendMutation.isPending}
				error={errorFor('rules')}
				apiReference={session.api_reference}
				// An API target always takes the approver's own rules: an
				// empty list would be default deny, and no fallback rule is
				// added on their behalf.
				requireRule={apiTarget}
			/>
		);
	}

	if (phase === 'secret' && secretKind) {
		const fieldHint =
			session.scheme && secretKind === 'api_key'
				? `Sent as ${schemeSummary(session.scheme)}.`
				: null;
		return (
			<SecretEntryStep
				header={
					<VendorHeader
						display={display}
						subtitle={secretStepSubtitle(secretKind, display.displayName)}
					/>
				}
				kind={secretKind}
				fieldHint={fieldHint}
				submitting={confirmMutation.isPending}
				error={errorFor('secret')}
				onBack={(): void => goToPhase('rules')}
				onSubmit={(secret): void => void submitSecret(secret)}
			/>
		);
	}

	if (phase === 'own_client') {
		const callbackUrl =
			providers.data?.providers?.find((p) => p.id === 'direct_oauth2')?.callback_url ?? null;
		return (
			<OwnOAuthClientStep
				header={
					<VendorHeader
						display={display}
						subtitle={`Enter the OAuth app you registered with ${display.displayName}.`}
					/>
				}
				callbackUrl={callbackUrl}
				catalogEndpoints={
					session.scheme?.authorization_url && session.scheme.token_url
						? {
								authorizeUrl: session.scheme.authorization_url,
								tokenUrl: session.scheme.token_url,
							}
						: null
				}
				submitting={confirmMutation.isPending}
				error={errorFor('own_client')}
				onBack={(): void => goToPhase('rules')}
				onSubmit={(client): void => void submitOwnClient(client)}
			/>
		);
	}

	const agentList = agents.data?.data ?? [];
	const agent = agentList.find((a) => a.id === boundAgentId);
	const currentSelection = selectedScopes ?? new Set<string>();
	const canConfirm = session.can_confirm;
	// The chooser shows when there is more than the session's own flow to
	// pick from — and always for `awaiting_app`, whose own flow is the
	// approver's OAuth client rather than a plain sign-in.
	const showChooser = canConfirm && (methodOptions.length > 1 || awaitingApp);
	const showScopes = method.kind === 'primary' && !secretKind;
	const continueDisabled =
		!canConfirm ||
		method.kind === 'shared_app' ||
		(method.kind === 'primary' &&
			!secretKind &&
			scopes.length > 0 &&
			currentSelection.size === 0);

	return (
		<div className="space-y-5">
			<VendorHeader
				display={display}
				subtitle={`An agent is asking to connect to ${display.displayName} on your behalf.`}
			/>

			{errorFor('configure') && <ErrorAlert message={errorFor('configure') ?? ''} />}

			<AgentRequestCard
				agent={
					agent ??
					(session.agent?.name
						? { id: session.agent.agent_id, name: session.agent.name }
						: undefined)
				}
				actorId={boundAgentId ?? session.requested_by_actor_id}
				ownerId={session.agent?.owner_id ?? null}
				reason={session.reason}
				loading={agents.isLoading}
			/>
			<PreviouslyRejected at={session.last_rejected_at} />

			{apiTarget && <ApiTargetDetails session={session} />}

			{!canConfirm && <ConfirmBlockedNotice reason={confirmBlockedReason(session)} />}

			{awaitingApp && canConfirm && (
				<p className="text-muted-foreground text-xs">
					{display.displayName} uses OAuth, and there is no OAuth app to connect through
					yet.
				</p>
			)}

			{showChooser && (
				<ConnectMethodField
					options={methodOptions}
					value={methodValue}
					onChange={(v): void => {
						setStepError(null);
						setMethodValue(v);
					}}
				/>
			)}

			{canConfirm && method.kind === 'shared_app' && sharedAppSurface && (
				<div className="bg-surface-inset rounded-lg p-3">
					{sharedAppSurface.renderRegister({
						api: session.api_reference,
						displayName: display.displayName,
						onRegistered: (): void => {
							setMethodValue('primary');
							void sessionQuery.refetch();
						},
					})}
				</div>
			)}

			{canConfirm && showScopes && (
				<ScopeChooseField
					loading={false}
					error={null}
					scopes={reviewScopesToCatalog(scopes)}
					selected={currentSelection}
					onToggle={toggleScope}
					agentRequested={new Set(scopes.filter((s) => s.requested).map((s) => s.name))}
				/>
			)}
			{canConfirm && showScopes && scopes.length > 0 && currentSelection.size === 0 && (
				<p className="text-muted-foreground text-xs" data-testid="scopes-needed-hint">
					Pick at least one scope to continue
					{scopes.some((s) => s.requested) ? '.' : ' — the agent didn’t ask for any.'}
				</p>
			)}

			<div className="bg-surface-sheet-foot border-hairline-field sticky -bottom-4 z-10 -mx-5 mt-auto -mb-4 flex items-center justify-between gap-2 border-t px-5 py-3.5">
				<div className="flex items-center gap-1">
					<Button type="button" variant="ghost" size="sm" onClick={handleNotNow}>
						{canConfirm ? 'Not now' : 'Close'}
					</Button>
					{canConfirm && (
						<Button
							type="button"
							variant="ghost"
							size="sm"
							className="text-danger hover:text-danger"
							onClick={(): void => {
								rejectMutation.reset();
								setRejectOpen(true);
							}}
						>
							Reject
						</Button>
					)}
				</div>
				{canConfirm && (
					<Button
						type="button"
						variant="primary"
						onClick={goToRules}
						// Scope-less vendors proceed with the vendor's defaults —
						// only gate on an empty selection when there are scopes
						// to choose from.
						disabled={continueDisabled}
					>
						Continue
					</Button>
				)}
			</div>

			<ConfirmDialog
				open={rejectOpen}
				title="Reject this request?"
				body={
					<div className="space-y-2">
						<p>
							The agent is told its request for {display.displayName} was rejected,
							and it can&apos;t ask again for this API for a while. To decide later
							instead, close this and choose Not now.
						</p>
						{rejectMutation.error && (
							<ErrorAlert
								message={
									(rejectMutation.error as { status?: number }).status === 403
										? "This request is no longer open, or it isn't yours to reject."
										: rejectMutation.error.message
								}
							/>
						)}
					</div>
				}
				confirmLabel="Reject request"
				pending={rejectMutation.isPending}
				onConfirm={(): void => void handleReject()}
				onClose={(): void => setRejectOpen(false)}
			/>
		</div>
	);
}

/** ReviewScope (session/review shape) → VendorScopeCatalog (choose-field shape). */
function reviewScopesToCatalog(scopes: ReviewScope[]): VendorScopeCatalog[] {
	return scopes.map((s) => ({
		name: s.name,
		classification: s.classification,
		default: s.default,
		description: s.description,
	}));
}
