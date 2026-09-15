import { useEffect, useMemo, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import {
	ArrowDown,
	ArrowLeft,
	ArrowUp,
	Bot,
	CheckCircle2,
	ExternalLink,
	Loader2,
	Plus,
	ShieldAlert,
	X,
	XCircle,
} from 'lucide-react';
import {
	AgentBadge,
	Badge,
	Button,
	Checkbox,
	CopyButton,
	ErrorAlert,
	Label,
	Skeleton,
	VendorIcon,
	toast,
} from '@/shared/ui';
import {
	useAgentsForPicker,
	useCancelConnectSession,
	useConfirmConnectSession,
	useConnectSession,
	usePollConnectSessionStatus,
	useStartAndConfirmVendorConnect,
	useVendorAuthCapabilities,
	useVendorOperations,
} from '@/shared/credentials/api/vendors-hooks';
import {
	cancelConnectSession,
	cancelConnectSessionBeacon,
} from '@/shared/credentials/api/vendors-client';
import { evaluateRules } from '@/shared/credentials/lib/rule-matcher';
import { isHttpsVendorUrl, openVendorUrl } from '@/shared/credentials/lib/safe-navigation';
import type {
	AuthCodeConfirmResponse,
	ConfirmResponse,
	DeviceAuthorizationConfirmResponse,
	PermissionRule,
	ReviewScope,
	ReviewSession,
	ScopeClassification,
	VendorScopeCatalog,
	VendorSummary,
} from '@/shared/credentials/api/vendors-types';

/**
 * Two modes:
 *
 *  * `self` — the current user is opening the flow from the credentials page,
 *    picks an agent + scopes, then runs start-and-confirm in one shot.
 *  * `approve` — an agent already started the session; the current user is the
 *    human owner following the emitted approval URL. Session (with the agent's
 *    requested scopes) already exists; we skip the picker, load the review data,
 *    and go straight to confirm → device code → poll.
 *
 * Both variants share the awaiting (device code + polling) and terminal
 * (success / failure) steps.
 */
export type VendorConnectFlowProps =
	| {
			mode: 'self';
			vendor: VendorSummary;
			onBack: () => void;
			onDone: () => void;
	  }
	| {
			mode: 'approve';
			sessionId: string;
			pollToken: string;
			onBack: () => void;
			onDone: () => void;
	  };

interface VendorDisplay {
	displayName: string;
	iconKey: string;
}

type Phase = 'configure' | 'rules' | 'awaiting' | 'terminal';

// Default preset the "Skip" button on the rules page persists:
// ``Allow: GET /*`` (methods list + prefix match). Not condition-less
// (path is constrained) so the backend model-validator accepts it.
const DEFAULT_ALLOW_GET_RULE: PermissionRule = {
	effect: 'allow',
	methods: ['GET'],
	path: '/',
	match_mode: 'prefix',
};

export function VendorConnectFlow(props: VendorConnectFlowProps) {
	if (props.mode === 'approve') {
		return (
			<VendorApproveFlow
				sessionId={props.sessionId}
				pollToken={props.pollToken}
				onBack={props.onBack}
				onDone={props.onDone}
			/>
		);
	}
	return (
		<VendorSelfConnectFlow vendor={props.vendor} onBack={props.onBack} onDone={props.onDone} />
	);
}

// ---------------------------------------------------------------------------
// Self-initiated flow
// ---------------------------------------------------------------------------

function VendorSelfConnectFlow({
	vendor,
	onBack,
	onDone,
}: {
	vendor: VendorSummary;
	onBack: () => void;
	onDone: () => void;
}) {
	const capabilities = useVendorAuthCapabilities(vendor.key);

	const queryClient = useQueryClient();
	const [selectedScopes, setSelectedScopes] = useState<Set<string>>(new Set());
	const [scopesTouched, setScopesTouched] = useState(false);
	const [phase, setPhase] = useState<Phase>('configure');
	const [rules, setRules] = useState<PermissionRule[] | null>(null);
	const [session, setSession] = useState<{ id: string; pollToken: string } | null>(null);
	const [challenge, setChallenge] = useState<ConfirmResponse | null>(null);

	const startMutation = useStartAndConfirmVendorConnect();
	const cancelMutation = useCancelConnectSession();

	// Cancellation is fire-and-forget on the unmount cleanup path (the
	// user closed the dialog or navigated away mid-flow); if we go
	// through the mutation, TanStack Query cancels the in-flight
	// request when the component unmounts and the backend never sees
	// it. Hold the effective (id, token) in a ref so the cleanup can
	// hit the raw client without depending on the mutation lifecycle.
	const sessionRef = useRef<{ id: string; pollToken: string } | null>(null);
	const phaseRef = useRef<Phase>('configure');

	const scopes = useMemo<VendorScopeCatalog[]>(
		() => capabilities.data?.scopes ?? [],
		[capabilities.data],
	);
	const scopeDefaults = useMemo(
		() => scopes.filter((s) => s.default).map((s) => s.name),
		[scopes],
	);

	useEffect(() => {
		if (scopesTouched) return;
		if (scopeDefaults.length === 0) return;
		setSelectedScopes(new Set(scopeDefaults));
	}, [scopeDefaults, scopesTouched]);

	const polling = usePollConnectSessionStatus(session?.id ?? '', session?.pollToken ?? '', {
		enabled: phase === 'awaiting' && !!session,
	});

	useEffect(() => {
		if (phase !== 'awaiting') return;
		// A 403 on /status means the backend deleted the session
		// (unhappy terminal — ``_mark_terminal`` cascades the credential
		// + session rather than leaving dangling ``failed`` rows). The
		// service raises ``InvalidPollTokenError`` uniformly for both
		// "session missing" and "poll_token mismatch" to close the
		// session-id enumeration oracle; we know the poll_token is
		// correct at this point (we made it through ``:confirm``), so
		// a 403 here is unambiguously "session gone → terminal".
		const err = polling.error as { status?: number } | undefined;
		if (err?.status === 403) {
			phaseRef.current = 'terminal';
			setPhase('terminal');
			return;
		}
		if (!polling.data) return;
		const status = polling.data.status;
		if (status === 'connected' || status === 'failed' || status === 'expired') {
			phaseRef.current = 'terminal';
			setPhase('terminal');
			if (status === 'connected') {
				toast({
					title: `Connected to ${vendor.display_name}`,
					description: polling.data.connected_as
						? `Signed in as ${polling.data.connected_as}.`
						: undefined,
					variant: 'success',
				});
				void queryClient.invalidateQueries({ queryKey: ['credentials'] });
			}
		}
	}, [phase, polling.data, polling.error, vendor.display_name, queryClient]);

	// Cancel-on-unmount: if the user closes the dialog / navigates away
	// mid-flow, the backend needs to know so the pending credential +
	// session get cleaned up. We fire the raw ``cancelConnectSession``
	// client (not the mutation) because TanStack Query aborts pending
	// mutations on unmount, and this call MUST reach the server.
	// Fire-and-forget; the backend is idempotent, so a double-cancel
	// (e.g. Cancel button click that also triggers unmount) is a 204.
	useEffect(() => {
		return () => {
			if (phaseRef.current !== 'awaiting') return;
			const s = sessionRef.current;
			if (!s) return;
			// Best-effort: swallow errors so a network blip on close
			// doesn't crash the parent tree.
			void cancelConnectSession(s.id, s.pollToken).catch(() => {});
		};
	}, []);

	// Tab-close variant: ``fetch`` fired from unmount is aborted by the
	// browser when the page itself is being torn down, so the pending
	// credential + session would otherwise linger until the session TTL
	// scanner reaps them. ``sendBeacon`` is guaranteed to deliver on
	// unload; we keep the ``fetch`` above for the in-page dismiss case
	// (dialog close, navigation) since it can observe the response.
	useEffect(() => {
		const onBeforeUnload = (): void => {
			if (phaseRef.current !== 'awaiting') return;
			const s = sessionRef.current;
			if (!s) return;
			cancelConnectSessionBeacon(s.id, s.pollToken);
		};
		window.addEventListener('beforeunload', onBeforeUnload);
		return () => window.removeEventListener('beforeunload', onBeforeUnload);
	}, []);

	const handleCancel = (): void => {
		const s = sessionRef.current;
		if (s && phase === 'awaiting') {
			cancelMutation.mutate({ sessionId: s.id, pollToken: s.pollToken });
			phaseRef.current = 'terminal';
		}
		onBack();
	};

	const toggleScope = (name: string) => {
		setScopesTouched(true);
		setSelectedScopes((prev) => {
			const next = new Set(prev);
			if (next.has(name)) next.delete(name);
			else next.add(name);
			return next;
		});
	};

	// Continue on the scopes page — no backend call yet. Seeds rules from the
	// scope classifications and hands off to the rules page for user review.
	const goToRules = (): void => {
		setRules((prev) => prev ?? derivePermissionRules(scopes, selectedScopes));
		setPhase('rules');
	};

	// Continue on the rules page — fires ``:connect`` + ``:confirm`` back-to-back
	// with the user's finalised rules.
	const startFlow = async (finalRules: PermissionRule[]) => {
		try {
			const result = await startMutation.mutateAsync({
				vendor: vendor.key,
				// agent_id intentionally omitted for now (self-driven flow — the
				// user IS the initiator). Bound to the current user's implicit
				// agent by the eventual agent-credential-binding migration.
				requested_scopes: Array.from(selectedScopes),
				permission_rules: finalRules,
			});
			sessionRef.current = { id: result.session_id, pollToken: result.poll_token };
			phaseRef.current = 'awaiting';
			setSession({ id: result.session_id, pollToken: result.poll_token });
			setChallenge(result.challenge);
			setPhase('awaiting');
			// Auth-code flows: pop the authorize URL open right away so the
			// human's next click is at the vendor, not back on this dialog.
			// Device flows: no auto-open — the user needs to copy the
			// user_code first, then click Open Vendor.
			// The URL is vendor-supplied — refuse to auto-navigate anything
			// that isn't https. On refusal, ``RedirectAwaitingStep`` renders
			// an ``UnsafeVendorUrlNotice`` in place of the open button.
			if (
				result.challenge.kind === 'authorization_code' &&
				isHttpsVendorUrl(result.challenge.authorize_url)
			) {
				openVendorUrl(result.challenge.authorize_url, '_blank', 'noopener,noreferrer');
			}
		} catch {
			// surfaced via ErrorAlert below.
		}
	};

	const display: VendorDisplay = {
		displayName: vendor.display_name,
		iconKey: vendor.vendor,
	};
	const startError = startMutation.error as Error | undefined;

	if (phase === 'terminal') {
		return (
			<TerminalStep
				display={display}
				status={polling.data?.status ?? 'failed'}
				connectedAs={polling.data?.connected_as ?? null}
				errorCode={polling.data?.error_code ?? null}
				onDone={onDone}
				onRetry={(): void => {
					setPhase('configure');
					setSession(null);
					setChallenge(null);
					startMutation.reset();
				}}
			/>
		);
	}

	if (phase === 'awaiting' && challenge) {
		return (
			<AwaitingStep
				display={display}
				challenge={challenge}
				status={polling.data?.status ?? 'pending'}
				onCancel={handleCancel}
			/>
		);
	}

	if (phase === 'rules') {
		return (
			<RulesStep
				display={display}
				requestedRules={[]}
				currentRules={rules ?? []}
				onChange={setRules}
				onBack={(): void => setPhase('configure')}
				onContinue={(finalRules: PermissionRule[]) => void startFlow(finalRules)}
				submitting={startMutation.isPending}
				error={startError ?? null}
				// Self flow: session/credential don't exist yet. The preview
				// will skeleton on "importing…" until the user Continues
				// (which fires ``:connect`` + ``:confirm`` + the catalog
				// import). Acceptable — the value of the preview lands in
				// the approve mode.
				apiReference={null}
			/>
		);
	}

	return (
		<div className="space-y-5">
			<VendorHeader
				display={display}
				subtitle={`You'll approve this connection on ${display.displayName} in a moment.`}
			/>

			<ScopeChooseField
				loading={capabilities.isLoading}
				error={capabilities.error as Error | null}
				scopes={scopes}
				selected={selectedScopes}
				onToggle={toggleScope}
			/>

			<div className="border-border bg-muted/20 -mx-5 -mb-4 flex items-center justify-between border-t px-5 py-3">
				<Button
					type="button"
					variant="ghost"
					size="sm"
					onClick={onBack}
					disabled={startMutation.isPending}
				>
					<ArrowLeft className="h-4 w-4" />
					Back
				</Button>
				<Button
					type="button"
					variant="primary"
					onClick={goToRules}
					disabled={selectedScopes.size === 0}
				>
					Continue
				</Button>
			</div>
		</div>
	);
}

// ---------------------------------------------------------------------------
// Agent-initiated approval flow
// ---------------------------------------------------------------------------

function VendorApproveFlow({
	sessionId,
	pollToken,
	onBack,
	onDone,
}: {
	sessionId: string;
	pollToken: string;
	onBack: () => void;
	onDone: () => void;
}) {
	const sessionQuery = useConnectSession(sessionId);
	const agents = useAgentsForPicker();
	const queryClient = useQueryClient();

	const [selectedScopes, setSelectedScopes] = useState<Set<string> | null>(null);
	const [phase, setPhase] = useState<Phase>('configure');
	const [rules, setRules] = useState<PermissionRule[] | null>(null);
	const [challenge, setChallenge] = useState<ConfirmResponse | null>(null);

	const confirmMutation = useConfirmConnectSession(sessionId);
	const cancelMutation = useCancelConnectSession();
	const phaseRef = useRef<Phase>('configure');

	const session: ReviewSession | undefined = sessionQuery.data;
	const scopes: ReviewScope[] = useMemo(() => session?.scopes ?? [], [session]);

	// Cancel-on-unmount mirror of the self-flow: if the user closes
	// the approval dialog mid-confirm, the pending credential +
	// session must be cleaned up server-side. See
	// ``VendorSelfConnectFlow`` for why we fire the raw client
	// instead of the mutation.
	useEffect(() => {
		return () => {
			if (phaseRef.current !== 'awaiting') return;
			void cancelConnectSession(sessionId, pollToken).catch(() => {});
		};
	}, [sessionId, pollToken]);

	// Tab-close variant — see the sibling ``VendorSelfConnectFlow`` effect
	// for the rationale (fetch aborts on unload; sendBeacon delivers).
	useEffect(() => {
		const onBeforeUnload = (): void => {
			if (phaseRef.current !== 'awaiting') return;
			cancelConnectSessionBeacon(sessionId, pollToken);
		};
		window.addEventListener('beforeunload', onBeforeUnload);
		return () => window.removeEventListener('beforeunload', onBeforeUnload);
	}, [sessionId, pollToken]);

	const handleCancel = (): void => {
		// Both configure- and awaiting-phase cancels must cancel the
		// server-side session. Without this the ``created`` session sits
		// pending until the TTL scanner reaps it (~30 min) and the
		// initiating agent's ``/status`` poll keeps reporting ``pending``
		// on an explicit human refusal.
		if (phase === 'awaiting' || phase === 'configure') {
			cancelMutation.mutate({ sessionId, pollToken });
			phaseRef.current = 'terminal';
		}
		onBack();
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

	const polling = usePollConnectSessionStatus(sessionId, pollToken, {
		enabled: phase === 'awaiting',
	});

	useEffect(() => {
		if (phase !== 'awaiting') return;
		// See ``VendorSelfConnectFlow`` for the 403-as-terminal rationale
		// (backend cascades the session on unhappy terminal, and the
		// service raises ``InvalidPollTokenError`` for missing sessions
		// to close the enumeration oracle — but we know our poll_token
		// is correct at this point, so a 403 here is unambiguously
		// "session gone → terminal").
		const err = polling.error as { status?: number } | undefined;
		if (err?.status === 403) {
			phaseRef.current = 'terminal';
			setPhase('terminal');
			return;
		}
		if (!polling.data) return;
		const status = polling.data.status;
		if (status === 'connected' || status === 'failed' || status === 'expired') {
			phaseRef.current = 'terminal';
			setPhase('terminal');
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
	// Rules are seeded from the agent's requested rules (from the review
	// payload) if present, otherwise from the classifications of the
	// selected scopes.
	const goToRules = (): void => {
		const chosen = Array.from(selectedScopes ?? []);
		setRules((prev) => {
			if (prev !== null) return prev;
			const requested = session?.requested_permission_rules ?? [];
			if (requested.length > 0) return requested;
			return derivePermissionRulesFromReview(scopes, new Set(chosen));
		});
		setPhase('rules');
	};

	// Continue on the rules page — fires ``:confirm`` with the user's
	// finalised rules (session already exists in approve mode).
	const approve = async (finalRules: PermissionRule[]) => {
		const chosen = Array.from(selectedScopes ?? []);
		try {
			const result = await confirmMutation.mutateAsync({
				confirmed_scopes: chosen,
				permission_rules: finalRules,
			});
			setChallenge(result);
			phaseRef.current = 'awaiting';
			setPhase('awaiting');
			// Vendor-supplied URL — https-only guard mirrors ``startFlow``.
			if (result.kind === 'authorization_code' && isHttpsVendorUrl(result.authorize_url)) {
				openVendorUrl(result.authorize_url, '_blank', 'noopener,noreferrer');
			}
		} catch {
			// surfaced via ErrorAlert below.
		}
	};

	if (sessionQuery.isLoading) {
		return (
			<div className="space-y-4">
				<Skeleton className="h-12 w-full" />
				<Skeleton className="h-24 w-full" />
			</div>
		);
	}
	if (sessionQuery.error || !session) {
		return (
			<div className="space-y-4">
				<ErrorAlert
					message={
						(sessionQuery.error as Error)?.message ??
						'The approval link is no longer valid.'
					}
				/>
				<div className="border-border bg-muted/20 -mx-5 -mb-4 flex items-center justify-end border-t px-5 py-3">
					<Button type="button" variant="ghost" size="sm" onClick={onDone}>
						Close
					</Button>
				</div>
			</div>
		);
	}

	const display: VendorDisplay = {
		displayName: session.vendor_display_name,
		iconKey: session.vendor_key,
	};

	if (phase === 'terminal') {
		return (
			<TerminalStep
				display={display}
				status={polling.data?.status ?? 'failed'}
				connectedAs={polling.data?.connected_as ?? null}
				errorCode={polling.data?.error_code ?? null}
				onDone={onDone}
			/>
		);
	}

	if (phase === 'awaiting' && challenge) {
		return (
			<AwaitingStep
				display={display}
				challenge={challenge}
				status={polling.data?.status ?? 'pending'}
				onCancel={handleCancel}
			/>
		);
	}

	if (phase === 'rules') {
		return (
			<RulesStep
				display={display}
				requestedRules={session.requested_permission_rules ?? []}
				currentRules={rules ?? []}
				onChange={setRules}
				onBack={(): void => setPhase('configure')}
				onContinue={(finalRules: PermissionRule[]) => void approve(finalRules)}
				submitting={confirmMutation.isPending}
				error={(confirmMutation.error as Error | null) ?? null}
				apiReference={session.api_reference}
			/>
		);
	}

	const agentList = agents.data?.data ?? [];
	const agent = agentList.find((a) => a.id === session.requested_by_actor_id);
	const currentSelection = selectedScopes ?? new Set<string>();

	return (
		<div className="space-y-5">
			<VendorHeader
				display={display}
				subtitle={`An agent is asking to connect to ${display.displayName} on your behalf.`}
			/>

			<AgentRequestCard
				agent={agent}
				actorId={session.requested_by_actor_id}
				reason={session.reason}
				loading={agents.isLoading}
			/>

			<ScopeChooseField
				loading={false}
				error={null}
				scopes={reviewScopesToCatalog(scopes)}
				selected={currentSelection}
				onToggle={toggleScope}
				agentRequested={new Set(scopes.filter((s) => s.requested).map((s) => s.name))}
			/>

			<div className="border-border bg-muted/20 -mx-5 -mb-4 flex items-center justify-between border-t px-5 py-3">
				<Button type="button" variant="ghost" size="sm" onClick={onBack}>
					Cancel
				</Button>
				<Button
					type="button"
					variant="primary"
					onClick={goToRules}
					disabled={currentSelection.size === 0}
				>
					Continue
				</Button>
			</div>
		</div>
	);
}

// ---------------------------------------------------------------------------
// Rules step — page 2 of the dialog
// ---------------------------------------------------------------------------

/**
 * Rules-review page. Sits between the scopes page and the vendor
 * round-trip. The user can either **Skip** (persists a single
 * ``Allow: GET /*`` preset) or accept the pre-populated rules (from
 * scope classifications, or from the agent's request in approve mode)
 * and Continue.
 *
 * Add/Edit/Remove and the operation-impact preview land in the
 * follow-up steps of the plan (step 5 + step 6).
 */
function RulesStep({
	display,
	requestedRules,
	currentRules,
	onChange,
	onBack,
	onContinue,
	submitting,
	error,
	apiReference,
}: {
	display: VendorDisplay;
	// Rules the initiating agent supplied on ``:connect``. Empty in the
	// self-flow. When non-empty, rows render with a "requested by agent"
	// pill (see below) so the human owner sees exactly what the agent
	// asked for. Kept as a distinct prop from ``currentRules`` — the
	// current list may diverge as the user edits, but the "requested"
	// tag on the ones that started life as agent-requested should stick.
	requestedRules: PermissionRule[];
	currentRules: PermissionRule[];
	onChange: (rules: PermissionRule[]) => void;
	onBack: () => void;
	onContinue: (finalRules: PermissionRule[]) => void;
	submitting: boolean;
	error: Error | null;
	// Where the vendor's OpenAPI lives. Null in the self-flow before
	// ``:connect`` fires; ``version`` null while the import is queued.
	// The operation-impact preview shows a skeleton until both are set
	// AND the ops endpoint returns 200.
	apiReference: { vendor: string; name: string | null; version: string | null } | null;
}) {
	const isEmpty = currentRules.length === 0;
	const previewRules = isEmpty ? [DEFAULT_ALLOW_GET_RULE] : currentRules;
	// Fast index for the "requested by agent" tag — deep-compare by
	// JSON since ``PermissionRule`` is a plain data shape and users
	// might edit rows in-place without changing identity.
	const requestedKeys = useMemo(
		() => new Set(requestedRules.map((r) => JSON.stringify(r))),
		[requestedRules],
	);

	const handleContinue = (): void => {
		const final = isEmpty ? [DEFAULT_ALLOW_GET_RULE] : currentRules;
		if (isEmpty) onChange(final);
		onContinue(final);
	};

	return (
		<div className="space-y-5">
			<VendorHeader
				display={display}
				subtitle={`Set what this credential lets an agent do on ${display.displayName}.`}
			/>

			<div className="space-y-2">
				<Label>Permission rules</Label>
				<p className="text-muted-foreground text-xs">
					{isEmpty
						? "We'll allow all read operations (GET) unless you set your own rules. Continue to accept, or add custom rules below."
						: 'First-match-wins. Requests that match no rule are denied.'}
				</p>
				<div className="border-border bg-muted/20 space-y-1.5 rounded-lg border p-2">
					{previewRules.map((rule, i) => (
						<RulePreviewRow
							key={i}
							rule={rule}
							isDefault={isEmpty}
							isRequested={requestedKeys.has(JSON.stringify(rule))}
							onDelete={
								isEmpty
									? undefined
									: (): void => onChange(currentRules.filter((_, j) => j !== i))
							}
							onMoveUp={
								isEmpty || i === 0
									? undefined
									: (): void => onChange(swap(currentRules, i, i - 1))
							}
							onMoveDown={
								isEmpty || i === currentRules.length - 1
									? undefined
									: (): void => onChange(swap(currentRules, i, i + 1))
							}
						/>
					))}
				</div>
				<AddRuleForm onAdd={(rule) => onChange([...currentRules, rule])} />
			</div>

			<OperationImpactPreview
				api={apiReference}
				rules={isEmpty ? previewRules : currentRules}
			/>

			{error && <ErrorAlert message={error} />}

			<div className="border-border bg-muted/20 -mx-5 -mb-4 flex items-center justify-between border-t px-5 py-3">
				<Button
					type="button"
					variant="ghost"
					size="sm"
					onClick={onBack}
					disabled={submitting}
				>
					<ArrowLeft className="h-4 w-4" />
					Back
				</Button>
				<Button
					type="button"
					variant="primary"
					onClick={handleContinue}
					loading={submitting}
				>
					{isEmpty ? 'Skip & continue' : 'Continue'}
				</Button>
			</div>
		</div>
	);
}

/**
 * One row in the rules editor. Read-only view of the rule + optional
 * delete + up/down controls when the row is editable (i.e. it's a real
 * user-authored rule, not the greyed-out default preset).
 */
function RulePreviewRow({
	rule,
	isDefault,
	isRequested,
	onDelete,
	onMoveUp,
	onMoveDown,
}: {
	rule: PermissionRule;
	isDefault: boolean;
	isRequested: boolean;
	onDelete?: () => void;
	onMoveUp?: () => void;
	onMoveDown?: () => void;
}) {
	const effectClass =
		rule.effect === 'allow'
			? 'bg-success/10 text-success border-success/40'
			: 'bg-danger/10 text-danger border-danger/40';
	const methodsLabel =
		rule.methods && rule.methods.length > 0 ? rule.methods.join(', ') : 'any method';
	return (
		<div
			className={`bg-background border-border flex items-center gap-2.5 rounded-md border px-2.5 py-1.5 text-xs ${
				isDefault ? 'opacity-70' : ''
			}`}
		>
			<span
				className={`rounded-md border px-1.5 py-0.5 font-mono text-[10px] tracking-wide uppercase ${effectClass}`}
			>
				{rule.effect}
			</span>
			<span className="text-foreground font-mono text-[11px]">{methodsLabel}</span>
			<span className="text-muted-foreground truncate font-mono text-[11px]">
				{rule.path ?? '/'}
				{rule.match_mode && rule.match_mode !== 'regex' ? ` (${rule.match_mode})` : ''}
			</span>
			{isRequested && (
				<Badge variant="default" className="ml-auto text-[10px]">
					requested by agent
				</Badge>
			)}
			{isDefault && !isRequested && (
				<span className="text-muted-foreground ml-auto text-[10px] italic">default</span>
			)}
			{(onMoveUp || onMoveDown || onDelete) && (
				<div className="ml-auto flex items-center gap-0.5">
					{onMoveUp && (
						<button
							type="button"
							className="text-muted-foreground hover:text-foreground p-0.5"
							aria-label="Move rule up"
							onClick={onMoveUp}
						>
							<ArrowUp className="h-3.5 w-3.5" />
						</button>
					)}
					{onMoveDown && (
						<button
							type="button"
							className="text-muted-foreground hover:text-foreground p-0.5"
							aria-label="Move rule down"
							onClick={onMoveDown}
						>
							<ArrowDown className="h-3.5 w-3.5" />
						</button>
					)}
					{onDelete && (
						<button
							type="button"
							className="text-muted-foreground hover:text-danger p-0.5"
							aria-label="Delete rule"
							onClick={onDelete}
						>
							<X className="h-3.5 w-3.5" />
						</button>
					)}
				</div>
			)}
		</div>
	);
}

/**
 * Operation-impact preview. Fetches the vendor's operations list and
 * renders each with an allow (green) / deny (red) pill computed from
 * the current rule set via the client-side matcher (parity-tested
 * against the Python matcher). No per-op HTTP call; rule edits are
 * instant.
 *
 * States:
 * * ``api == null`` or ``version == null`` → "still importing" skeleton
 *   (session hasn't kicked the import yet, or import job is queued).
 * * fetch returned ``null`` (404) → same skeleton, hook is polling.
 * * fetch returned an empty page → "no operations imported yet" note.
 * * fetch returned data → render.
 */
function OperationImpactPreview({
	api,
	rules,
}: {
	api: { vendor: string; name: string | null; version: string | null } | null;
	rules: readonly PermissionRule[];
}) {
	const ops = useVendorOperations(api ?? undefined, { enabled: !!api });
	const items = ops.data?.data ?? [];
	const importing = !api || !api.name || !api.version || ops.data == null;

	return (
		<div className="space-y-2">
			<Label>What this credential lets an agent do</Label>
			{importing ? (
				<div className="border-border bg-muted/20 rounded-lg border px-3 py-6 text-center">
					<Loader2 className="text-muted-foreground mx-auto h-4 w-4 animate-spin" />
					<p className="text-muted-foreground mt-2 text-xs">
						Operations still importing — this preview will fill in shortly.
					</p>
				</div>
			) : items.length === 0 ? (
				<div className="border-border bg-muted/20 rounded-lg border px-3 py-4 text-center">
					<p className="text-muted-foreground text-xs">
						No operations imported for this vendor yet.
					</p>
				</div>
			) : (
				<div className="border-border max-h-56 space-y-1 overflow-y-auto rounded-lg border p-2">
					{items.map((op) => {
						const allowed = evaluateRules(rules, {
							method: op.method,
							path: op.path,
							operation_id: op.operation_id,
						});
						return (
							<div
								key={op.operation_id}
								className="bg-background border-border flex items-center gap-2.5 rounded-md border px-2.5 py-1.5 text-xs"
							>
								<span
									className={`rounded-md border px-1.5 py-0.5 font-mono text-[10px] uppercase ${
										allowed
											? 'bg-success/10 text-success border-success/40'
											: 'bg-danger/10 text-danger border-danger/40'
									}`}
									aria-label={allowed ? 'allowed' : 'denied'}
								>
									{allowed ? 'allow' : 'deny'}
								</span>
								<span className="text-muted-foreground font-mono text-[10px] uppercase">
									{op.method}
								</span>
								<span className="text-foreground truncate font-mono text-[11px]">
									{op.path}
								</span>
								{op.name && (
									<span className="text-muted-foreground ml-auto truncate text-[10px]">
										{op.name}
									</span>
								)}
							</div>
						);
					})}
				</div>
			)}
		</div>
	);
}

// Swap two elements in an array immutably — used for move-up / move-down.
function swap<T>(items: T[], i: number, j: number): T[] {
	const next = [...items];
	[next[i], next[j]] = [next[j], next[i]];
	return next;
}

/**
 * Compact inline form for authoring a new ``PermissionRule``. Mirrors
 * the backend ``PermissionRuleSchema`` field-by-field + reimplements
 * ``_reject_condition_less_allow`` client-side so the user gets an
 * inline error instead of a 422 from the server on Continue.
 */
function AddRuleForm({ onAdd }: { onAdd: (rule: PermissionRule) => void }) {
	const [open, setOpen] = useState(false);
	const [effect, setEffect] = useState<'allow' | 'deny'>('allow');
	const [methods, setMethods] = useState<Set<string>>(new Set());
	const [path, setPath] = useState('');
	const [matchMode, setMatchMode] = useState<'regex' | 'prefix' | 'exact'>('prefix');
	const [error, setError] = useState<string | null>(null);

	const reset = (): void => {
		setEffect('allow');
		setMethods(new Set());
		setPath('');
		setMatchMode('prefix');
		setError(null);
	};

	const toggleMethod = (method: string): void => {
		setMethods((prev) => {
			const next = new Set(prev);
			if (next.has(method)) next.delete(method);
			else next.add(method);
			return next;
		});
	};

	const handleSave = (): void => {
		const hasMethods = methods.size > 0;
		const hasPath = path.trim().length > 0;
		if (effect === 'allow' && !hasMethods && !hasPath) {
			setError('An "allow" rule must constrain at least one of methods or path.');
			return;
		}
		const rule: PermissionRule = {
			effect,
			methods: hasMethods ? Array.from(methods) : null,
			path: hasPath ? path.trim() : null,
			match_mode: matchMode,
		};
		onAdd(rule);
		setOpen(false);
		reset();
	};

	if (!open) {
		return (
			<Button
				type="button"
				variant="ghost"
				size="sm"
				className="w-full justify-start"
				onClick={(): void => setOpen(true)}
			>
				<Plus className="h-3.5 w-3.5" />
				Add rule
			</Button>
		);
	}

	return (
		<div className="border-border bg-background space-y-2 rounded-lg border p-3">
			<div className="flex items-center gap-2">
				<Label className="text-[11px]">Effect</Label>
				<div className="flex gap-1">
					{(['allow', 'deny'] as const).map((e) => (
						<button
							key={e}
							type="button"
							onClick={(): void => setEffect(e)}
							className={`rounded-md border px-2 py-0.5 font-mono text-[10px] uppercase ${
								effect === e
									? e === 'allow'
										? 'bg-success/15 text-success border-success/50'
										: 'bg-danger/15 text-danger border-danger/50'
									: 'text-muted-foreground border-border'
							}`}
						>
							{e}
						</button>
					))}
				</div>
			</div>

			<div className="flex flex-wrap items-center gap-2">
				<Label className="text-[11px]">Methods</Label>
				<div className="flex flex-wrap gap-1">
					{['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].map((m) => (
						<button
							key={m}
							type="button"
							onClick={(): void => toggleMethod(m)}
							className={`rounded-md border px-1.5 py-0.5 font-mono text-[10px] ${
								methods.has(m)
									? 'bg-primary/15 text-primary border-primary/40'
									: 'text-muted-foreground border-border'
							}`}
						>
							{m}
						</button>
					))}
				</div>
			</div>

			<div className="flex items-center gap-2">
				<Label className="text-[11px]">Path</Label>
				<input
					type="text"
					value={path}
					onChange={(e): void => setPath(e.target.value)}
					placeholder="/repos"
					className="border-border bg-background flex-1 rounded-md border px-2 py-1 font-mono text-[11px]"
				/>
				<select
					value={matchMode}
					onChange={(e): void => setMatchMode(e.target.value as typeof matchMode)}
					className="border-border bg-background rounded-md border px-2 py-1 font-mono text-[10px]"
				>
					<option value="prefix">prefix</option>
					<option value="exact">exact</option>
					<option value="regex">regex</option>
				</select>
			</div>

			{error && <p className="text-danger text-[11px]">{error}</p>}

			<div className="flex items-center justify-end gap-2 pt-1">
				<Button
					type="button"
					variant="ghost"
					size="sm"
					onClick={(): void => {
						setOpen(false);
						reset();
					}}
				>
					Cancel
				</Button>
				<Button type="button" variant="primary" size="sm" onClick={handleSave}>
					Add
				</Button>
			</div>
		</div>
	);
}

// ---------------------------------------------------------------------------
// Shared subcomponents
// ---------------------------------------------------------------------------

function VendorHeader({ display, subtitle }: { display: VendorDisplay; subtitle: string }) {
	return (
		<div className="flex items-center gap-3">
			<VendorIcon name={display.displayName} vendor={display.iconKey} size="lg" />
			<div>
				<p className="text-foreground text-base font-semibold">{display.displayName}</p>
				<p className="text-muted-foreground text-xs">{subtitle}</p>
			</div>
		</div>
	);
}

function AgentRequestCard({
	agent,
	actorId,
	reason,
	loading,
}: {
	agent: { id: string; name: string; description?: string | null } | undefined;
	actorId: string;
	// Free-text ``reason`` the agent supplied on ``POST /integrations:connect``
	// — the single piece of context that justifies the whole review page.
	// Rendered as a distinct block below the agent identity so the human sees
	// *why* alongside *who*.
	reason: string | null;
	loading: boolean;
}) {
	return (
		<div className="space-y-2">
			<Label>Requested by</Label>
			<div className="bg-muted/40 border-border flex items-center gap-2.5 rounded-lg border px-3 py-2">
				{loading ? (
					<Skeleton className="h-7 w-7 rounded-md" />
				) : agent ? (
					<AgentBadge id={agent.id} name={agent.name} size="sm" />
				) : (
					<div className="bg-muted flex h-7 w-7 shrink-0 items-center justify-center rounded-md">
						<Bot className="text-muted-foreground h-3.5 w-3.5" />
					</div>
				)}
				<div className="min-w-0 flex-1">
					<p className="text-foreground truncate text-sm font-medium">
						{agent?.name ?? 'Agent'}
					</p>
					<p className="text-muted-foreground truncate font-mono text-[11px]">
						{actorId}
					</p>
				</div>
			</div>
			{reason && (
				<div className="bg-muted/20 border-border rounded-lg border px-3 py-2">
					<p className="text-muted-foreground text-[10px] font-medium tracking-wide uppercase">
						Reason
					</p>
					<p className="text-foreground mt-1 text-sm whitespace-pre-wrap">{reason}</p>
				</div>
			)}
		</div>
	);
}

function ScopeChooseField({
	loading,
	error,
	scopes,
	selected,
	onToggle,
	agentRequested,
}: {
	loading: boolean;
	error: Error | null;
	scopes: VendorScopeCatalog[];
	selected: Set<string>;
	onToggle: (name: string) => void;
	/** Set of scope names the initiating agent explicitly requested; renders a
	 * subtle "requested" tag next to those rows so the approving human sees
	 * which items were added by the agent vs. carried over from defaults. */
	agentRequested?: Set<string>;
}) {
	if (loading) {
		return (
			<div className="space-y-2">
				<Label>What can this agent do?</Label>
				<div className="space-y-1.5">
					<Skeleton className="h-12 w-full" />
					<Skeleton className="h-12 w-full" />
					<Skeleton className="h-12 w-full" />
				</div>
			</div>
		);
	}
	if (error) return <ErrorAlert message={error.message} />;
	if (scopes.length === 0) {
		return (
			<div className="border-border bg-muted/30 rounded-lg border border-dashed p-4">
				<p className="text-muted-foreground text-xs">
					This integration doesn&apos;t expose any scopes — the connection will use the
					vendor&apos;s defaults.
				</p>
			</div>
		);
	}
	return (
		<div className="space-y-2">
			<div className="flex items-baseline justify-between">
				<Label>What can this agent do?</Label>
				<span className="text-muted-foreground text-xs">
					{selected.size} of {scopes.length} selected
				</span>
			</div>
			<div className="border-border divide-border divide-y overflow-hidden rounded-lg border">
				{scopes.map((scope) => (
					<ScopeRow
						key={scope.name}
						scope={scope}
						checked={selected.has(scope.name)}
						onToggle={(): void => onToggle(scope.name)}
						agentRequested={agentRequested?.has(scope.name) ?? false}
					/>
				))}
			</div>
		</div>
	);
}

function ScopeRow({
	scope,
	checked,
	onToggle,
	agentRequested,
}: {
	scope: VendorScopeCatalog;
	checked: boolean;
	onToggle: () => void;
	agentRequested: boolean;
}) {
	return (
		<label className="hover:bg-muted/40 flex cursor-pointer items-start gap-3 px-3 py-2.5 transition-colors">
			<Checkbox checked={checked} onChange={onToggle} className="mt-0.5" />
			<div className="min-w-0 flex-1">
				<div className="flex items-center gap-2">
					<code className="text-foreground text-sm font-medium">{scope.name}</code>
					<ScopeClassificationBadge classification={scope.classification} />
					{agentRequested && (
						<Badge variant="default" className="text-[10px]">
							requested
						</Badge>
					)}
				</div>
				{scope.description && (
					<p className="text-muted-foreground mt-0.5 text-xs">{scope.description}</p>
				)}
			</div>
		</label>
	);
}

function ScopeClassificationBadge({ classification }: { classification: ScopeClassification }) {
	const variant =
		classification === 'read' ? 'success' : classification === 'write' ? 'warning' : 'danger';
	const label = classification.charAt(0).toUpperCase() + classification.slice(1);
	return (
		<Badge variant={variant} className="text-[10px]">
			{label}
		</Badge>
	);
}

/**
 * Renders the "waiting for user to approve at vendor" step. Discriminates
 * on ``challenge.kind`` and hands off to one of two focused subcomponents —
 * the shapes are genuinely different (device code panel vs. redirect
 * prompt), so a single component with `challenge.user_code &&` branches
 * would just be pretending they're one thing.
 */
function AwaitingStep({
	display,
	challenge,
	status,
	onCancel,
}: {
	display: VendorDisplay;
	challenge: ConfirmResponse;
	status: string;
	onCancel: () => void;
}) {
	if (challenge.kind === 'device_authorization') {
		return (
			<DeviceCodeAwaitingStep
				display={display}
				challenge={challenge}
				status={status}
				onCancel={onCancel}
			/>
		);
	}
	return (
		<RedirectAwaitingStep
			display={display}
			challenge={challenge}
			status={status}
			onCancel={onCancel}
		/>
	);
}

function DeviceCodeAwaitingStep({
	display,
	challenge,
	status,
	onCancel,
}: {
	display: VendorDisplay;
	challenge: DeviceAuthorizationConfirmResponse;
	status: string;
	onCancel: () => void;
}) {
	const openUrl = challenge.verification_uri_complete ?? challenge.verification_uri ?? null;
	return (
		<div className="space-y-5">
			<div className="flex items-center gap-3">
				<VendorIcon name={display.displayName} vendor={display.iconKey} size="lg" />
				<div>
					<p className="text-foreground text-base font-semibold">
						Almost there — approve on {display.displayName}
					</p>
					<p className="text-muted-foreground text-xs">
						Enter the code below when {display.displayName} asks for it.
					</p>
				</div>
			</div>

			{challenge.user_code && (
				<div className="border-border bg-muted/30 flex flex-col items-center gap-3 rounded-xl border border-dashed p-6">
					<p className="text-muted-foreground font-mono text-[10px] tracking-widest uppercase">
						Your one-time code
					</p>
					<div className="flex items-center gap-3">
						<code className="text-foreground bg-background border-border rounded-lg border px-4 py-2 font-mono text-2xl font-semibold tracking-widest">
							{challenge.user_code}
						</code>
						<CopyButton value={challenge.user_code} />
					</div>
				</div>
			)}

			{openUrl &&
				(isHttpsVendorUrl(openUrl) ? (
					<Button
						type="button"
						variant="primary"
						className="w-full"
						onClick={(): void => {
							openVendorUrl(openUrl, '_blank', 'noopener,noreferrer');
						}}
					>
						<ExternalLink className="h-4 w-4" />
						Open {display.displayName}
					</Button>
				) : (
					<UnsafeVendorUrlNotice />
				))}

			<PollingStatusLine display={display} status={status} />
			<CancelBar onCancel={onCancel} />
		</div>
	);
}

function RedirectAwaitingStep({
	display,
	challenge,
	status,
	onCancel,
}: {
	display: VendorDisplay;
	challenge: AuthCodeConfirmResponse;
	status: string;
	onCancel: () => void;
}) {
	return (
		<div className="space-y-5">
			<div className="flex items-center gap-3">
				<VendorIcon name={display.displayName} vendor={display.iconKey} size="lg" />
				<div>
					<p className="text-foreground text-base font-semibold">
						Almost there — approve on {display.displayName}
					</p>
					<p className="text-muted-foreground text-xs">
						Complete the sign-in in the {display.displayName} window that just opened.
					</p>
				</div>
			</div>

			{isHttpsVendorUrl(challenge.authorize_url) ? (
				<Button
					type="button"
					variant="primary"
					className="w-full"
					onClick={(): void => {
						openVendorUrl(challenge.authorize_url, '_blank', 'noopener,noreferrer');
					}}
				>
					<ExternalLink className="h-4 w-4" />
					Re-open {display.displayName}
				</Button>
			) : (
				<UnsafeVendorUrlNotice />
			)}

			<PollingStatusLine display={display} status={status} />
			<CancelBar onCancel={onCancel} />
		</div>
	);
}

/**
 * Shown in place of the "Open <vendor>" button when the vendor's OAuth response
 * carried a non-https URL. See ``lib/safe-navigation.ts`` for the guard rules.
 */
function UnsafeVendorUrlNotice() {
	return (
		<div className="border-destructive/40 bg-destructive/10 text-destructive flex items-start gap-2 rounded-lg border px-3 py-2.5 text-xs">
			<ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
			<p>
				The vendor returned a sign-in link that isn't a secure HTTPS URL. For safety we
				won't open it — cancel and try again.
			</p>
		</div>
	);
}

function PollingStatusLine({ display, status }: { display: VendorDisplay; status: string }) {
	return (
		<div className="border-border bg-muted/20 flex items-center gap-2.5 rounded-lg border px-3 py-2.5">
			<Loader2 className="text-muted-foreground h-4 w-4 shrink-0 animate-spin" />
			<p className="text-muted-foreground text-xs">
				{status === 'polling' || status === 'pending'
					? `Waiting for you to approve at ${display.displayName}…`
					: 'Checking status…'}
			</p>
		</div>
	);
}

function CancelBar({ onCancel }: { onCancel: () => void }) {
	return (
		<div className="border-border bg-muted/20 -mx-5 -mb-4 flex items-center justify-end border-t px-5 py-3">
			<Button type="button" variant="ghost" size="sm" onClick={onCancel}>
				Cancel
			</Button>
		</div>
	);
}

function TerminalStep({
	display,
	status,
	connectedAs,
	errorCode,
	onDone,
	onRetry,
}: {
	display: VendorDisplay;
	status: string;
	connectedAs: string | null;
	errorCode: string | null;
	onDone: () => void;
	// Approve-mode has no meaningful "retry" — once the session hits
	// terminal, the agent must initiate a new one. Callers in that mode
	// omit ``onRetry`` and the button is hidden.
	onRetry?: () => void;
}) {
	const success = status === 'connected';
	return (
		<div className="space-y-5">
			<div className="flex flex-col items-center gap-3 py-4 text-center">
				{success ? (
					<CheckCircle2 className="text-success h-10 w-10" />
				) : (
					<XCircle className="text-danger h-10 w-10" />
				)}
				<div>
					<p className="text-foreground text-base font-semibold">
						{success
							? `Connected to ${display.displayName}`
							: status === 'expired'
								? 'The code expired'
								: 'Sign-in failed'}
					</p>
					{success && connectedAs && (
						<p className="text-muted-foreground mt-1 text-sm">
							Signed in as <span className="font-mono">{connectedAs}</span>
						</p>
					)}
					{!success && errorCode && (
						<p className="text-muted-foreground mt-1 text-xs">
							Reason: <span className="font-mono">{errorCode}</span>
						</p>
					)}
				</div>
			</div>

			<div className="border-border bg-muted/20 -mx-5 -mb-4 flex items-center justify-end gap-2 border-t px-5 py-3">
				{!success && onRetry && (
					<Button type="button" variant="secondary" onClick={onRetry}>
						Try again
					</Button>
				)}
				<Button type="button" variant="primary" onClick={onDone}>
					Done
				</Button>
			</div>
		</div>
	);
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** ReviewScope (session/review shape) → VendorScopeCatalog (choose-field shape). */
function reviewScopesToCatalog(scopes: ReviewScope[]): VendorScopeCatalog[] {
	return scopes.map((s) => ({
		name: s.name,
		classification: s.classification,
		default: s.default,
		description: s.description,
	}));
}

/**
 * Derive minimal read/write permission rules from the selected scopes. Kept
 * lock-step with the backend expectation on `:confirm` — the human doesn't see
 * these; they're a platform-side gate the vendor scopes imply.
 */
function derivePermissionRules(
	catalog: VendorScopeCatalog[],
	selected: Set<string>,
): PermissionRule[] {
	const chosen = catalog.filter((s) => selected.has(s.name));
	return rulesForClassifications(chosen.map((s) => s.classification));
}

function derivePermissionRulesFromReview(
	scopes: ReviewScope[],
	selected: Set<string>,
): PermissionRule[] {
	const chosen = scopes.filter((s) => selected.has(s.name));
	return rulesForClassifications(chosen.map((s) => s.classification));
}

function rulesForClassifications(classifications: ScopeClassification[]): PermissionRule[] {
	const hasRead = classifications.includes('read');
	const hasWrite = classifications.includes('write') || classifications.includes('admin');
	const rules: PermissionRule[] = [];
	if (hasRead) rules.push({ method: 'GET', path: '/**', effect: 'allow' });
	if (hasWrite) {
		for (const method of ['POST', 'PUT', 'PATCH', 'DELETE']) {
			rules.push({ method, path: '/**', effect: 'allow' });
		}
	}
	return rules;
}
