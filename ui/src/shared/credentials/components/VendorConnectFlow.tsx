import type { ReactNode } from 'react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import {
	ArrowLeft,
	Bot,
	CheckCircle2,
	ExternalLink,
	Loader2,
	ShieldAlert,
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
	useStartIntegrationConnect,
	useVendorAuthCapabilities,
	useVendorOperations,
} from '@/shared/credentials/api/vendors-hooks';
import {
	cancelConnectSession,
	cancelConnectSessionBeacon,
} from '@/shared/credentials/api/vendors-client';
import { OperationImpactPreview } from '@/shared/credentials/components/OperationImpactPreview';
import {
	DefaultRulePreviewRow,
	RuleListEditor,
} from '@/shared/credentials/components/RuleListEditor';
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
/**
 * Info handed to ``renderPostConnect`` on a successful connect. Callers
 * use it to build the "Bind to more agents" CTA (or anything else) —
 * that lives in ``modules/agents`` because layering forbids ``shared/``
 * from importing module code, so this component takes a render prop
 * instead of composing the CTA inline.
 */
export interface PostConnectInfo {
	credentialId: string;
	// The agent the credential was just bound to at ``:confirm``. Null
	// only for the (rare) case where the session had no target agent —
	// callers can decide whether to show anything in that state.
	boundAgentId: string | null;
}

export type VendorConnectFlowProps =
	| {
			mode: 'self';
			vendor: VendorSummary;
			// When set, the flow opens with the given agent pre-selected and
			// the picker rendered disabled. Used by the "Bind credential"
			// entry from an agent's detail page so the user can't
			// accidentally re-target during binding.
			preselectedAgentId?: string;
			// Extra content rendered on the terminal step's success path
			// (typically a "Bind to more agents" CTA). See ``PostConnectInfo``.
			renderPostConnect?: (info: PostConnectInfo) => ReactNode;
			onBack: () => void;
			onDone: () => void;
	  }
	| {
			mode: 'approve';
			sessionId: string;
			pollToken: string;
			renderPostConnect?: (info: PostConnectInfo) => ReactNode;
			onBack: () => void;
			onDone: () => void;
	  };

interface VendorDisplay {
	displayName: string;
	iconKey: string;
}

type Phase = 'configure' | 'rules' | 'awaiting' | 'terminal';

// Fallback the client injects into the ``:confirm`` request when the
// user reaches the rules page and leaves the list empty — ``Allow: GET /*``
// (methods list + prefix match). Not condition-less (path is
// constrained) so the backend model-validator accepts it. Only fires
// at Continue-click; the rules editor itself never renders this as a
// row so the user always sees exactly what they authored, and the
// empty state describes the fallback in prose.
const DEFAULT_ALLOW_GET_RULE: PermissionRule = {
	effect: 'allow',
	methods: ['GET'],
	path: '/',
	match_mode: 'prefix',
};

// Stable empty reference for the ``pathSuggestions`` default so the
// form's ``useMemo`` doesn't invalidate on every render when the caller
// omits it.
const EMPTY_PATHS: readonly string[] = [];

export function VendorConnectFlow(props: VendorConnectFlowProps) {
	if (props.mode === 'approve') {
		return (
			<VendorApproveFlow
				sessionId={props.sessionId}
				pollToken={props.pollToken}
				renderPostConnect={props.renderPostConnect}
				onBack={props.onBack}
				onDone={props.onDone}
			/>
		);
	}
	return (
		<VendorSelfConnectFlow
			vendor={props.vendor}
			preselectedAgentId={props.preselectedAgentId}
			renderPostConnect={props.renderPostConnect}
			onBack={props.onBack}
			onDone={props.onDone}
		/>
	);
}

// ---------------------------------------------------------------------------
// Self-initiated flow
// ---------------------------------------------------------------------------

function VendorSelfConnectFlow({
	vendor,
	preselectedAgentId,
	renderPostConnect,
	onBack,
	onDone,
}: {
	vendor: VendorSummary;
	preselectedAgentId?: string;
	renderPostConnect?: (info: PostConnectInfo) => ReactNode;
	onBack: () => void;
	onDone: () => void;
}) {
	const capabilities = useVendorAuthCapabilities(vendor.key);
	const agents = useAgentsForPicker();

	const queryClient = useQueryClient();
	const [selectedScopes, setSelectedScopes] = useState<Set<string>>(new Set());
	const [scopesTouched, setScopesTouched] = useState(false);
	const [phase, setPhase] = useState<Phase>('configure');
	const [rules, setRules] = useState<PermissionRule[] | null>(null);
	const [session, setSession] = useState<{ id: string; pollToken: string } | null>(null);
	const [challenge, setChallenge] = useState<ConfirmResponse | null>(null);
	// When ``preselectedAgentId`` is supplied by the caller (entry from
	// an agent's detail page), the picker starts locked to that id.
	// Otherwise it starts empty and the user must pick before Continue.
	const [agentId, setAgentId] = useState<string | null>(preselectedAgentId ?? null);

	const startMutation = useStartIntegrationConnect();
	const confirmMutation = useConfirmConnectSession(session?.id ?? '');
	const cancelMutation = useCancelConnectSession();
	// Post-``:connect`` we can hydrate ``ReviewSession`` (specifically
	// ``api_reference``) to feed the rules-page operation-impact preview.
	// Gated on the session id existing so we don't fire before ``:connect``
	// returns.
	const reviewSession = useConnectSession(session?.id, { enabled: !!session?.id });

	// Cancellation is fire-and-forget on the unmount cleanup path (the
	// user closed the dialog or navigated away mid-flow); if we go
	// through the mutation, TanStack Query cancels the in-flight
	// request when the component unmounts and the backend never sees
	// it. Hold the effective (id, token) in a ref so the cleanup can
	// hit the raw client without depending on the mutation lifecycle.
	const sessionRef = useRef<{ id: string; pollToken: string } | null>(null);
	const phaseRef = useRef<Phase>('configure');
	// StrictMode dev-time mounts effects twice. Without this guard the
	// ``:connect`` fires twice and we get two orphaned sessions per open.
	const connectFiredRef = useRef(false);

	// Fire ``:connect`` on mount so the session/credential/import all
	// exist by the time the user reaches the rules page — the same
	// shape the approve flow lands in when the human hits the URL.
	useEffect(() => {
		if (connectFiredRef.current) return;
		connectFiredRef.current = true;
		void (async () => {
			try {
				const result = await startMutation.mutateAsync({ vendor: vendor.key });
				sessionRef.current = { id: result.session_id, pollToken: result.poll_token };
				setSession({ id: result.session_id, pollToken: result.poll_token });
			} catch {
				// surfaced via ErrorAlert on the configure page.
			}
		})();
		// startMutation is stable across renders (react-query hook); vendor.key
		// only changes when the parent remounts the flow, at which point the
		// ref resets naturally.
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [vendor.key]);

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
			// Any pre-terminal phase — ``:connect`` fires at mount, so a
			// session exists from ``configure`` onward. Without cancelling
			// on unmount from those earlier phases too, a user who opens
			// the dialog and immediately closes it leaves a pending
			// credential + session until the TTL scanner reaps them.
			if (phaseRef.current === 'terminal') return;
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
			if (phaseRef.current === 'terminal') return;
			const s = sessionRef.current;
			if (!s) return;
			cancelConnectSessionBeacon(s.id, s.pollToken);
		};
		window.addEventListener('beforeunload', onBeforeUnload);
		return () => window.removeEventListener('beforeunload', onBeforeUnload);
	}, []);

	const handleCancel = (): void => {
		// Session exists from ``configure`` onward (``:connect`` fires at
		// mount). Any pre-terminal cancel must clean up server-side —
		// otherwise the pending credential/session sit until TTL. Same
		// reasoning as ``VendorApproveFlow.handleCancel``.
		const s = sessionRef.current;
		if (s && phase !== 'terminal') {
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

	// Continue on the scopes page — no backend call yet. Rules start empty;
	// the empty-state message on the rules page tells the user that
	// leaving it empty falls back to allow-all-GETs at ``:confirm`` time.
	// We do not pre-populate rules from scope classifications — the user
	// sees exactly what they authored, nothing more.
	const goToRules = (): void => {
		setRules((prev) => prev ?? []);
		setPhase('rules');
	};

	// Continue on the rules page — session already exists (``:connect``
	// fired at mount). Just POST ``:confirm`` with the human-approved
	// scopes + rules + selected agent, and transition to ``awaiting``.
	// ``agent_id`` lands at ``:confirm`` (not ``:connect``) so the
	// session-on-vendor-click semantics are preserved for the self flow
	// — see the backend's late-bind branch in ``ConnectSessionService.confirm``.
	const confirmFlow = async (finalRules: PermissionRule[]) => {
		if (!session) return;
		try {
			const result = await confirmMutation.mutateAsync({
				confirmed_scopes: Array.from(selectedScopes),
				permission_rules: finalRules,
				agent_id: agentId,
			});
			phaseRef.current = 'awaiting';
			setChallenge(result);
			setPhase('awaiting');
			// The URL is vendor-supplied — refuse to auto-navigate anything
			// that isn't https. On refusal, ``RedirectAwaitingStep`` renders
			// an ``UnsafeVendorUrlNotice`` in place of the open button.
			if (result.kind === 'authorization_code' && isHttpsVendorUrl(result.authorize_url)) {
				openVendorUrl(result.authorize_url, '_blank', 'noopener,noreferrer');
			}
		} catch {
			// surfaced via ErrorAlert below.
		}
	};

	const display: VendorDisplay = {
		displayName: vendor.display_name,
		iconKey: vendor.vendor,
	};
	// Errors from either the on-mount ``:connect`` or the rules-page
	// ``:confirm`` — surfaced on whichever page the user is looking at.
	const flowError =
		(startMutation.error as Error | null) ?? (confirmMutation.error as Error | null);

	if (phase === 'terminal') {
		return (
			<TerminalStep
				display={display}
				status={polling.data?.status ?? 'failed'}
				connectedAs={polling.data?.connected_as ?? null}
				errorCode={polling.data?.error_code ?? null}
				credentialId={polling.data?.credential_id ?? null}
				renderPostConnect={renderPostConnect}
				boundAgentId={agentId}
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
				onContinue={(finalRules: PermissionRule[]) => void confirmFlow(finalRules)}
				submitting={confirmMutation.isPending}
				error={flowError}
				// Session exists from mount — ``api_reference`` comes back
				// on the review-session read once ``:connect`` completes,
				// so by the time the rules page mounts the ops-list fetch
				// has real coords to work with.
				apiReference={reviewSession.data?.api_reference ?? null}
			/>
		);
	}

	return (
		<div className="space-y-5">
			<VendorHeader
				display={display}
				subtitle={`You'll approve this connection on ${display.displayName} in a moment.`}
			/>

			<AgentPickerField
				agents={agents.data?.data ?? []}
				loading={agents.isLoading}
				error={agents.error as Error | null}
				value={agentId}
				onChange={setAgentId}
				disabled={preselectedAgentId != null}
			/>

			<ScopeChooseField
				loading={capabilities.isLoading}
				error={capabilities.error as Error | null}
				scopes={scopes}
				selected={selectedScopes}
				onToggle={toggleScope}
			/>

			{flowError && <ErrorAlert message={flowError} />}

			<div className="border-border bg-muted/20 -mx-5 -mb-4 flex items-center justify-between border-t px-5 py-3">
				<Button type="button" variant="ghost" size="sm" onClick={handleCancel}>
					<ArrowLeft className="h-4 w-4" />
					Back
				</Button>
				<Button
					type="button"
					variant="primary"
					onClick={goToRules}
					// ``:connect`` fires at mount — wait for the session id
					// before letting the user advance so the rules page has
					// something to attach to when it renders. Also require
					// an agent — the credential must bind to one, and the
					// server refuses to accept ``agent_id: null`` at
					// ``:confirm`` because there's nothing to bind against.
					disabled={selectedScopes.size === 0 || !session || !agentId}
					loading={startMutation.isPending && !session}
				>
					Continue
				</Button>
			</div>
		</div>
	);
}

/**
 * Agent-selection dropdown on the self-flow configure page. Renders
 * ``agent.name`` — actor IDs are non-obvious identifiers, so surfacing
 * them would only confuse the user. Empty state (no agents in this
 * user's account) shows an inline note pointing at agent-creation.
 * ``disabled`` locks the field to its current value so the "Bind
 * credential" entry from an agent's detail page can pre-select without
 * risk of accidental re-target.
 */
function AgentPickerField({
	agents,
	loading,
	error,
	value,
	onChange,
	disabled,
}: {
	agents: readonly { id: string; name: string }[];
	loading: boolean;
	error: Error | null;
	value: string | null;
	onChange: (id: string | null) => void;
	disabled: boolean;
}) {
	if (loading) {
		return (
			<div className="space-y-2">
				<Label>Which agent uses this credential?</Label>
				<Skeleton className="h-9 w-full" />
			</div>
		);
	}
	if (error) return <ErrorAlert message={error.message} />;
	if (agents.length === 0) {
		return (
			<div className="space-y-2">
				<Label>Which agent uses this credential?</Label>
				<div className="border-border bg-muted/30 rounded-lg border border-dashed p-3">
					<p className="text-muted-foreground text-xs">
						You don&apos;t have any agents yet. Create one first, then come back to
						connect the credential.
					</p>
				</div>
			</div>
		);
	}
	return (
		<div className="space-y-2">
			<Label htmlFor="connect-agent-picker">Which agent uses this credential?</Label>
			<select
				id="connect-agent-picker"
				value={value ?? ''}
				onChange={(e): void => onChange(e.target.value || null)}
				disabled={disabled}
				className="border-border bg-background text-foreground disabled:text-muted-foreground w-full rounded-md border px-3 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-70"
			>
				<option value="" disabled>
					Select an agent…
				</option>
				{agents.map((a) => (
					<option key={a.id} value={a.id}>
						{a.name}
					</option>
				))}
			</select>
		</div>
	);
}

// ---------------------------------------------------------------------------
// Agent-initiated approval flow
// ---------------------------------------------------------------------------

function VendorApproveFlow({
	sessionId,
	pollToken,
	renderPostConnect,
	onBack,
	onDone,
}: {
	sessionId: string;
	pollToken: string;
	renderPostConnect?: (info: PostConnectInfo) => ReactNode;
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
	// Rules seed from the agent's ``requested_permission_rules`` when the
	// agent supplied any (those are agent-authored, not client-guessed);
	// otherwise the list stays empty and the empty-state fallback kicks
	// in at ``:confirm`` time, same as the self flow.
	const goToRules = (): void => {
		setRules((prev) => {
			if (prev !== null) return prev;
			return session?.requested_permission_rules ?? [];
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
				credentialId={polling.data?.credential_id ?? null}
				renderPostConnect={renderPostConnect}
				boundAgentId={session.requested_by_actor_id}
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
 * round-trip.
 *
 * Empty state: the rules list stays empty in local state but the UI
 * shows a single greyed-out ``Allow GET /`` preview row so the user
 * can SEE the rule that will land on Continue. The row's ``default``
 * tag signals it's not user-authored. Only when the user clicks
 * Continue does the client inject that default into the ``:confirm``
 * request body — local state stays empty either way.
 *
 * Non-empty: rules render in first-match-wins order with reorder +
 * delete controls. Agent-requested rules (approve mode) carry a
 * "requested by agent" pill.
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

	// Operation-impact preview: when the user has authored no rules,
	// preview against the confirm-time fallback so they see what
	// leaving the list empty actually grants. When they've authored
	// rules, preview against those exactly.
	const previewRules = isEmpty ? [DEFAULT_ALLOW_GET_RULE] : currentRules;

	// Fetch ops once at the rules-page level and thread the result down.
	// React-Query dedupes by query key so ``OperationImpactPreview``'s
	// own ``useVendorOperations`` call hits the shared cache without
	// re-fetching. Paths feed the shared rule-list editor's
	// autocomplete + no-ops-affected warning.
	const opsQuery = useVendorOperations(apiReference ?? undefined, {
		enabled: !!apiReference,
	});
	const pathSuggestions = useMemo<readonly string[]>(() => {
		const rows = opsQuery.data?.data;
		if (!rows) return EMPTY_PATHS;
		return Array.from(new Set(rows.map((op) => op.path))).sort();
	}, [opsQuery.data]);

	const handleContinue = (): void => {
		const final = isEmpty ? [DEFAULT_ALLOW_GET_RULE] : currentRules;
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
				<RuleListEditor
					rules={currentRules}
					onChange={onChange}
					pathSuggestions={pathSuggestions}
					opTemplates={pathSuggestions}
					opsLoaded={pathSuggestions.length > 0}
					requestedRules={requestedRules}
					emptyStateContent={<DefaultRulePreviewRow rule={DEFAULT_ALLOW_GET_RULE} />}
				/>
			</div>

			<OperationImpactPreview api={apiReference} rules={previewRules} />

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
	credentialId,
	boundAgentId,
	renderPostConnect,
	onDone,
	onRetry,
}: {
	display: VendorDisplay;
	status: string;
	connectedAs: string | null;
	errorCode: string | null;
	// The credential id the successful connect wrote — from
	// ``/status.credential_id``. Handed to ``renderPostConnect`` for
	// the "bind to more agents" CTA. ``null`` when the flow failed or
	// hasn't yielded a credential row.
	credentialId: string | null;
	// The agent the credential just got bound to at ``:confirm``. The
	// bind-more picker excludes this one so the list shows only NEW
	// targets.
	boundAgentId: string | null;
	// Post-connect extra content — see ``PostConnectInfo``. Callers
	// supply this from ``modules/agents`` (the ``shared/`` layer
	// can't import module code).
	renderPostConnect?: (info: PostConnectInfo) => ReactNode;
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

			{success && credentialId && renderPostConnect?.({ credentialId, boundAgentId })}

			<div className="border-border bg-muted/20 -mx-5 -mb-4 flex items-center justify-end gap-2 border-t px-5 py-3">
				{!success && onRetry && (
					<Button type="button" variant="secondary" onClick={onRetry}>
						Try again
					</Button>
				)}
				<Button type="button" variant="primary" onClick={onDone}>
					{success ? 'Close' : 'Done'}
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
