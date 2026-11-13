import type { ReactNode } from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
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
	ActorLabel,
	AgentBadge,
	Badge,
	Button,
	Checkbox,
	CopyButton,
	ErrorAlert,
	Label,
	Skeleton,
	VendorIcon,
} from '@/shared/ui';
import { useVendorOperations } from '@/shared/credentials/api/vendors-hooks';
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
	ScopeClassification,
	StatusResponse,
	VendorScopeCatalog,
} from '@/shared/credentials/api/vendors-types';
import type { PostConnectInfo, VendorDisplay } from './vendorFlowTypes';

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

/** Why a scope change's vendor sign-in ended without applying it. */
export function signInFailure(status: StatusResponse | undefined): string {
	if (status?.status === 'expired') {
		return 'The sign-in expired before the scopes were granted. Nothing changed.';
	}
	if (status?.error_code === 'scopes_not_removed') {
		return 'The vendor kept scopes this change removes, so nothing changed. Revoke the app’s access at the vendor to drop them, then try again.';
	}
	if (status?.error_code === 'binding_now_shared') {
		return 'Another agent was bound to this credential while you were signing in, and the wider scopes would reach it too, so nothing changed. Review the request again.';
	}
	return 'The scopes weren’t granted, so nothing changed.';
}

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
export function RulesStep({
	display,
	requestedRules,
	currentRules,
	onChange,
	onBack,
	onContinue,
	submitting,
	error,
	apiReference,
	requireRule = false,
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
	error: Error | string | null;
	/**
	 * Require at least one authored rule: no ``Allow GET /`` fallback is
	 * previewed or sent, and Continue stays disabled on an empty list.
	 */
	requireRule?: boolean;
	// Where the vendor's OpenAPI lives. Null in the self-flow before
	// ``:connect`` fires; ``version`` null while the import is queued.
	// The operation-impact preview shows a skeleton until both are set
	// AND the ops endpoint returns 200.
	apiReference: { vendor: string; name: string | null; version: string | null } | null;
}) {
	const isEmpty = currentRules.length === 0;
	const fallback = isEmpty && !requireRule;

	// Operation-impact preview: when the user has authored no rules,
	// preview against the confirm-time fallback so they see what
	// leaving the list empty actually grants. When they've authored
	// rules, preview against those exactly.
	const previewRules = fallback ? [DEFAULT_ALLOW_GET_RULE] : currentRules;

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
		if (isEmpty && requireRule) return;
		const final = fallback ? [DEFAULT_ALLOW_GET_RULE] : currentRules;
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
					{fallback
						? "We'll allow all read operations (GET) unless you set your own rules. Continue to accept, or add custom rules below."
						: isEmpty
							? "Add at least one rule. With no rules the agent can't call anything on this API."
							: 'First-match-wins. Requests that match no rule are denied.'}
				</p>
				<RuleListEditor
					rules={currentRules}
					onChange={onChange}
					pathSuggestions={pathSuggestions}
					opTemplates={pathSuggestions}
					opsLoaded={pathSuggestions.length > 0}
					requestedRules={requestedRules}
					emptyStateContent={
						requireRule ? undefined : (
							<DefaultRulePreviewRow rule={DEFAULT_ALLOW_GET_RULE} />
						)
					}
				/>
			</div>

			<OperationImpactPreview api={apiReference} rules={previewRules} />

			{error && <ErrorAlert message={error} />}

			<div className="bg-surface-sheet-foot border-hairline-field sticky -bottom-4 z-10 -mx-5 mt-auto -mb-4 flex items-center justify-between border-t px-5 py-3.5">
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
					disabled={isEmpty && requireRule}
				>
					{fallback ? 'Skip & continue' : 'Continue'}
				</Button>
			</div>
		</div>
	);
}

export function VendorHeader({ display, subtitle }: { display: VendorDisplay; subtitle: string }) {
	return (
		<div className="flex items-center gap-3">
			<VendorIcon name={display.displayName} vendor={display.iconKey} size="lg" />
			<div className="min-w-0">
				<p className="text-foreground text-base font-semibold">{display.displayName}</p>
				<SharedAppLine display={display} />
				<p className="text-muted-foreground text-xs">{subtitle}</p>
			</div>
		</div>
	);
}

export function SharedAppLine({ display }: { display: VendorDisplay }) {
	if (!display.sharedAppName) return null;
	return (
		<p className="mt-0.5 flex min-w-0 items-center gap-1.5 text-xs">
			<Badge variant="default">Shared app</Badge>
			<span className="text-muted-foreground truncate">via {display.sharedAppName}</span>
		</p>
	);
}

export function AgentRequestCard({
	agent,
	actorId,
	ownerId = null,
	reason,
	loading,
}: {
	agent: { id: string; name: string; description?: string | null } | undefined;
	actorId: string;
	/** The agent's owner, from the review (server data); null when unclaimed or unknown. */
	ownerId?: string | null;
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
			<div className="bg-surface-inset flex items-center gap-2.5 rounded-lg px-3 py-2">
				{loading ? (
					<Skeleton className="h-7 w-7 rounded-md" />
				) : agent ? (
					<AgentBadge id={agent.id} name={agent.name} size="sm" />
				) : (
					<div className="bg-surface-chip flex h-7 w-7 shrink-0 items-center justify-center rounded-md">
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
				{ownerId && (
					<p className="text-muted-foreground shrink-0 text-xs">
						{/* No type prefix: "Owner" already says who this is. */}
						Owner <ActorLabel actorId={ownerId} />
					</p>
				)}
			</div>
			{reason && (
				<div className="bg-surface-inset rounded-lg px-3 py-2">
					<p className="text-foreground-faint text-[10.5px] font-bold tracking-[0.08em] uppercase">
						Reason
					</p>
					<p className="text-foreground mt-1 text-sm whitespace-pre-wrap">{reason}</p>
				</div>
			)}
		</div>
	);
}

/** That this agent's ask for the same target was rejected before, and when. */
export function PreviouslyRejected({ at }: { at?: string | null }) {
	if (!at) return null;
	return (
		<p role="note" className="text-warning flex items-center gap-2 text-sm">
			<ShieldAlert className="h-4 w-4" />
			This agent asked for this before, and the request was rejected on{' '}
			{new Date(at).toLocaleString()}.
		</p>
	);
}

export function ScopeChooseField({
	loading,
	error,
	scopes,
	selected,
	onToggle,
	agentRequested,
	label = 'What can this agent do?',
}: {
	/** The field's heading. */
	label?: string;
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
				<Label>{label}</Label>
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
			<div className="bg-surface-inset rounded-lg p-4">
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
				<Label>{label}</Label>
				<span className="text-muted-foreground text-xs">
					{selected.size} of {scopes.length} selected
				</span>
			</div>
			<div className="bg-surface-inset divide-hairline-row divide-y overflow-hidden rounded-lg">
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
		<label className="hover:bg-tint-2 flex cursor-pointer items-start gap-3 px-3 py-2.5 transition-colors">
			<Checkbox checked={checked} onChange={onToggle} className="mt-0.5" />
			<div className="min-w-0 flex-1">
				<div className="flex items-center gap-2">
					<code className="text-foreground text-sm font-medium">{scope.name}</code>
					<ScopeClassificationBadge classification={scope.classification} />
					{agentRequested && <Badge variant="default">requested by agent</Badge>}
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
	return <Badge variant={variant}>{label}</Badge>;
}

/**
 * Renders the "waiting for user to approve at vendor" step. Discriminates
 * on ``challenge.kind`` and hands off to one of two focused subcomponents —
 * the shapes are genuinely different (device code panel vs. redirect
 * prompt), so a single component with `challenge.user_code &&` branches
 * would just be pretending they're one thing.
 */
export function AwaitingStep({
	display,
	challenge,
	status,
	cancelling,
	cancelError,
	onCancel,
}: {
	display: VendorDisplay;
	challenge: ConfirmResponse;
	status: string;
	cancelling?: boolean;
	cancelError?: string | null;
	onCancel: () => void;
}) {
	if (challenge.kind === 'device_authorization') {
		return (
			<DeviceCodeAwaitingStep
				display={display}
				challenge={challenge}
				status={status}
				cancelling={cancelling}
				cancelError={cancelError}
				onCancel={onCancel}
			/>
		);
	}
	return (
		<RedirectAwaitingStep
			display={display}
			challenge={challenge}
			status={status}
			cancelling={cancelling}
			cancelError={cancelError}
			onCancel={onCancel}
		/>
	);
}

function DeviceCodeAwaitingStep({
	display,
	challenge,
	status,
	cancelling,
	cancelError,
	onCancel,
}: {
	display: VendorDisplay;
	challenge: DeviceAuthorizationConfirmResponse;
	status: string;
	cancelling?: boolean;
	cancelError?: string | null;
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
				<div className="bg-surface-inset flex flex-col items-center gap-3 rounded-lg p-6">
					<p className="text-foreground-faint text-[10.5px] font-bold tracking-[0.08em] uppercase">
						Your one-time code
					</p>
					<div className="flex items-center gap-3">
						<code className="text-foreground bg-surface-chip rounded-lg px-4 py-2 font-mono text-2xl font-semibold tracking-widest">
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
			{cancelError && <ErrorAlert message={cancelError} />}
			<CancelBar onCancel={onCancel} loading={cancelling} />
		</div>
	);
}

function RedirectAwaitingStep({
	display,
	challenge,
	status,
	cancelling,
	cancelError,
	onCancel,
}: {
	display: VendorDisplay;
	challenge: AuthCodeConfirmResponse;
	status: string;
	cancelling?: boolean;
	cancelError?: string | null;
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
			{cancelError && <ErrorAlert message={cancelError} />}
			<CancelBar onCancel={onCancel} loading={cancelling} />
		</div>
	);
}

/**
 * Shown in place of the "Open <vendor>" button when the vendor's OAuth response
 * carried a non-https URL. See ``lib/safe-navigation.ts`` for the guard rules.
 */
function UnsafeVendorUrlNotice() {
	return (
		<div className="bg-destructive/10 text-destructive flex items-start gap-2 rounded-lg px-3 py-2.5 text-xs">
			<ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
			<p>
				The vendor returned a sign-in link that isn&apos;t a secure HTTPS URL. For safety we
				won&apos;t open it — cancel and try again.
			</p>
		</div>
	);
}

function PollingStatusLine({ display, status }: { display: VendorDisplay; status: string }) {
	return (
		<div className="bg-surface-inset flex items-center gap-2.5 rounded-lg px-3 py-2.5">
			<Loader2 className="text-muted-foreground h-4 w-4 shrink-0 animate-spin" />
			<p className="text-muted-foreground text-xs">
				{status === 'polling' || status === 'pending'
					? `Waiting for you to approve at ${display.displayName}…`
					: 'Checking status…'}
			</p>
		</div>
	);
}

function CancelBar({ onCancel, loading }: { onCancel: () => void; loading?: boolean }) {
	return (
		<div className="bg-surface-sheet-foot border-hairline-field sticky -bottom-4 z-10 -mx-5 mt-auto -mb-4 flex items-center justify-end border-t px-5 py-3.5">
			<Button type="button" variant="ghost" size="sm" loading={loading} onClick={onCancel}>
				Cancel
			</Button>
		</div>
	);
}

export function TerminalStep({
	display,
	status,
	connectedAs,
	errorCode,
	credentialId,
	boundAgentId,
	renderPostConnect,
	onDone,
	onRetry,
	failureMessage,
	children,
}: {
	display: VendorDisplay;
	status: string;
	connectedAs: string | null;
	errorCode: string | null;
	/** Plain-words reason for a failure the dialog itself observed (e.g. on `:confirm`). */
	failureMessage?: string;
	/** Extra content under the outcome (e.g. the re-authorize follow-up). */
	children?: ReactNode;
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
	const success = status === 'connected' || status === 'applied';
	// A finished connect closes itself after a short countdown — unless the step
	// asks for something more (the re-authorize notice), or the operator does
	// anything in it (pick agents to bind, Stay open), which keeps it open.
	const [remaining, setRemaining] = useState<number | null>(
		success && !children ? AUTO_CLOSE_SECONDS : null,
	);
	const stay = useCallback((): void => setRemaining(null), []);
	useEffect(() => {
		if (remaining == null) return undefined;
		if (remaining <= 0) {
			onDone();
			return undefined;
		}
		const tick = setTimeout(() => setRemaining((r) => (r == null ? null : r - 1)), 1000);
		return () => clearTimeout(tick);
	}, [remaining, onDone]);
	return (
		<div className="space-y-5" onPointerDown={stay} onKeyDown={stay}>
			<div className="flex flex-col items-center gap-3 py-4 text-center">
				{success ? (
					<CheckCircle2 className="text-success h-10 w-10" />
				) : (
					<XCircle className="text-danger h-10 w-10" />
				)}
				<div>
					<p className="text-foreground text-base font-semibold">
						{success
							? status === 'applied'
								? `Access to ${display.displayName} updated`
								: `Connected to ${display.displayName}`
							: status === 'expired'
								? 'The code expired'
								: failureMessage
									? "This request can't be approved"
									: 'Sign-in failed'}
					</p>
					{success && connectedAs && (
						<p className="text-muted-foreground mt-1 text-sm">
							Signed in as <span className="font-mono">{connectedAs}</span>
						</p>
					)}
					{!success && failureMessage && (
						<p className="text-muted-foreground mt-1 text-sm">{failureMessage}</p>
					)}
					{!success && !failureMessage && errorCode && (
						<p className="text-muted-foreground mt-1 text-xs">
							Reason: <span className="font-mono">{errorCode}</span>
						</p>
					)}
				</div>
			</div>

			{children}

			{success && credentialId && renderPostConnect?.({ credentialId, boundAgentId })}

			<div className="bg-surface-sheet-foot border-hairline-field sticky -bottom-4 z-10 -mx-5 mt-auto -mb-4 flex items-center justify-end gap-2 border-t px-5 py-3.5">
				{remaining != null && (
					<span className="text-muted-foreground mr-auto flex items-center gap-2 text-sm">
						{/* Announced once; the visible count ticks without re-announcing. */}
						<span className="sr-only" role="status">
							{`This closes by itself in ${AUTO_CLOSE_SECONDS} seconds.`}
						</span>
						<span aria-hidden="true" data-testid="auto-close-countdown">
							Closing in {remaining}…
						</span>
						<Button type="button" variant="ghost" size="sm" onClick={stay}>
							Stay open
						</Button>
					</span>
				)}
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

/** Seconds a finished connect stays on screen before it closes by itself. */
const AUTO_CLOSE_SECONDS = 5;
