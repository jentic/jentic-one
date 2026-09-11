import { useEffect, useMemo, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, Bot, CheckCircle2, ExternalLink, Loader2, XCircle } from 'lucide-react';
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
	useConfirmConnectSession,
	useConnectSession,
	usePollConnectSessionStatus,
	useStartAndConfirmVendorConnect,
	useVendorAuthCapabilities,
} from '@/shared/credentials/api/vendors-hooks';
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

type Phase = 'configure' | 'awaiting' | 'terminal';

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
	const [session, setSession] = useState<{ id: string; pollToken: string } | null>(null);
	const [challenge, setChallenge] = useState<ConfirmResponse | null>(null);

	const startMutation = useStartAndConfirmVendorConnect();

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
		// A 404 on /status means the backend deleted the session
		// (unhappy terminal: the credential + its session were cleaned
		// up rather than left as dangling ``failed`` rows). Treat as
		// terminal-failed and stop polling.
		const err = polling.error as { status?: number } | undefined;
		if (err?.status === 404) {
			setPhase('terminal');
			return;
		}
		if (!polling.data) return;
		const status = polling.data.status;
		if (status === 'connected' || status === 'failed' || status === 'expired') {
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

	const toggleScope = (name: string) => {
		setScopesTouched(true);
		setSelectedScopes((prev) => {
			const next = new Set(prev);
			if (next.has(name)) next.delete(name);
			else next.add(name);
			return next;
		});
	};

	const startFlow = async () => {
		try {
			const result = await startMutation.mutateAsync({
				vendor: vendor.key,
				// agent_id intentionally omitted — credentials still bind
				// through toolkits, so the picker was purely cosmetic. Will
				// be surfaced again as a required field once agent-credential
				// bindings replace toolkit membership.
				requested_scopes: Array.from(selectedScopes),
				permission_rules: derivePermissionRules(scopes, selectedScopes),
			});
			setSession({ id: result.session_id, pollToken: result.poll_token });
			setChallenge(result.challenge);
			setPhase('awaiting');
			// Auth-code flows: pop the authorize URL open right away so the
			// human's next click is at the vendor, not back on this dialog.
			// Device flows: no auto-open — the user needs to copy the
			// user_code first, then click Open Vendor.
			if (result.challenge.kind === 'authorization_code') {
				window.open(result.challenge.authorize_url, '_blank', 'noopener,noreferrer');
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
				onCancel={onBack}
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

			{startError && <ErrorAlert message={startError} />}

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
					onClick={(): void => void startFlow()}
					loading={startMutation.isPending}
					disabled={selectedScopes.size === 0}
				>
					Continue to {vendor.display_name}
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
	const [challenge, setChallenge] = useState<ConfirmResponse | null>(null);

	const confirmMutation = useConfirmConnectSession(sessionId);

	const session: ReviewSession | undefined = sessionQuery.data;
	const scopes: ReviewScope[] = useMemo(() => session?.scopes ?? [], [session]);

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
		// See ``VendorSelfConnectFlow`` for the 404-as-terminal rationale
		// (backend deletes the session on unhappy terminal outcomes so
		// no dangling ``failed`` credential lingers in the UI).
		const err = polling.error as { status?: number } | undefined;
		if (err?.status === 404) {
			setPhase('terminal');
			return;
		}
		if (!polling.data) return;
		const status = polling.data.status;
		if (status === 'connected' || status === 'failed' || status === 'expired') {
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

	const approve = async () => {
		const chosen = Array.from(selectedScopes ?? []);
		try {
			const rules = derivePermissionRulesFromReview(scopes, new Set(chosen));
			const result = await confirmMutation.mutateAsync({
				confirmed_scopes: chosen,
				permission_rules: rules,
			});
			setChallenge(result);
			setPhase('awaiting');
			if (result.kind === 'authorization_code') {
				window.open(result.authorize_url, '_blank', 'noopener,noreferrer');
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
				onRetry={onDone}
			/>
		);
	}

	if (phase === 'awaiting' && challenge) {
		return (
			<AwaitingStep
				display={display}
				challenge={challenge}
				status={polling.data?.status ?? 'pending'}
				onCancel={onBack}
			/>
		);
	}

	const agentList = agents.data?.data ?? [];
	const agent = agentList.find((a) => a.id === session.requested_by_actor_id);
	const confirmError = confirmMutation.error as Error | undefined;
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

			{confirmError && <ErrorAlert message={confirmError} />}

			<div className="border-border bg-muted/20 -mx-5 -mb-4 flex items-center justify-between border-t px-5 py-3">
				<Button
					type="button"
					variant="ghost"
					size="sm"
					onClick={onBack}
					disabled={confirmMutation.isPending}
				>
					Cancel
				</Button>
				<Button
					type="button"
					variant="primary"
					onClick={(): void => void approve()}
					loading={confirmMutation.isPending}
					disabled={currentSelection.size === 0}
				>
					Approve &amp; continue
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
	loading,
}: {
	agent: { id: string; name: string; description?: string | null } | undefined;
	actorId: string;
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

			{openUrl && (
				<Button
					type="button"
					variant="primary"
					className="w-full"
					onClick={(): void => {
						window.open(openUrl, '_blank', 'noopener,noreferrer');
					}}
				>
					<ExternalLink className="h-4 w-4" />
					Open {display.displayName}
				</Button>
			)}

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

			<Button
				type="button"
				variant="primary"
				className="w-full"
				onClick={(): void => {
					window.open(challenge.authorize_url, '_blank', 'noopener,noreferrer');
				}}
			>
				<ExternalLink className="h-4 w-4" />
				Re-open {display.displayName}
			</Button>

			<PollingStatusLine display={display} status={status} />
			<CancelBar onCancel={onCancel} />
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
	onRetry: () => void;
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
				{!success && (
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
