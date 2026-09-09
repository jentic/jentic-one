/**
 * ApprovalQueue — the D7 DCR approval queue, rebuilt origins-first.
 *
 * Anti-spoofing posture (the #1264 authorize-page stance, GitHub's OAuth-app
 * review grammar): the row HEADLINE is the verifiable signal — the
 * redirect-URI origins plus the RFC 7591 `software_id` — while the
 * attacker-chosen `client_name` is demoted to secondary text explicitly
 * labelled "self-reported name". Each row also shows the allowed scopes (if
 * restricted), the consent-model chip, and registered-`timeAgo` provenance
 * (+`created_by` for admin-registered rows).
 *
 * Approve activates the client (approved+active atomically); Deny keeps the
 * row (reversible — the Denied filter re-offers Approve as the recovery
 * path, without a second Deny). The deny-reason dialog is mounted
 * persistently and keeps its draft across a casual dismiss (dialog-state
 * rule); the draft resets when the TARGET changes, which also covers the
 * committed-deny path (the parent clears the target on success).
 */
import { useEffect, useRef, useState } from 'react';
import { CheckCircle2 } from 'lucide-react';
import {
	ActorLabel,
	Badge,
	Button,
	CopyButton,
	Dialog,
	EmptyState,
	ErrorAlert,
	Input,
	Label,
	LoadingState,
	SegmentedToggle,
	toast,
	type SegmentedToggleOption,
} from '@/shared/ui';
import { formatTimestamp, timeAgo } from '@/shared/lib/utils';
import {
	useApproveOAuthClient,
	useDenyOAuthClient,
	useOAuthClientQueue,
	type OAuthClient,
} from '@/modules/settings/api/hooks';
import { clientOrigins } from '@/modules/settings/components/clientStatus';
import { ClientStatusBadges, ClientTypeChips } from '@/modules/settings/components/clientBadges';

export type QueueFilter = 'pending' | 'denied';

const QUEUE_FILTER_OPTIONS: SegmentedToggleOption<QueueFilter>[] = [
	{ value: 'pending', label: 'Pending' },
	{ value: 'denied', label: 'Denied' },
];

interface DenyClientDialogProps {
	/** The client under decision; null only before the first Deny click. */
	client: OAuthClient | null;
	open: boolean;
	onClose: () => void;
	onConfirm: (reason: string) => void;
	isPending: boolean;
}

/**
 * Deny confirmation with an optional reason draft. Mounted once and toggled
 * via `open` (dialog-state rule: persist between dismissals, reset on
 * successful commit) — a casual Esc/backdrop dismiss keeps the half-typed
 * reason. The draft resets when the TARGET changes (a different client's
 * denial is a different draft), which also covers the success path: the
 * parent clears the target after a committed deny.
 */
function DenyClientDialog({ client, open, onClose, onConfirm, isPending }: DenyClientDialogProps) {
	const [reason, setReason] = useState('');
	const lastIdRef = useRef(client?.id);
	useEffect(() => {
		if (lastIdRef.current !== client?.id) {
			lastIdRef.current = client?.id;
			setReason('');
		}
	}, [client?.id]);
	return (
		<Dialog
			open={open && client != null}
			onClose={onClose}
			title="Deny OAuth Client?"
			footer={
				<>
					<Button variant="outline" onClick={onClose}>
						Cancel
					</Button>
					<Button
						variant="danger"
						onClick={(): void => onConfirm(reason.trim())}
						disabled={isPending}
					>
						{isPending ? 'Denying...' : 'Deny'}
					</Button>
				</>
			}
		>
			<div className="space-y-3">
				<p className="text-muted-foreground">
					<strong>{client?.name}</strong> will not be able to start authorization flows.
					The registration is kept, so you can approve it later to reverse this.
				</p>
				<div>
					<Label htmlFor="deny-reason">Reason (optional)</Label>
					<Input
						id="deny-reason"
						value={reason}
						onChange={(e): void => setReason(e.target.value)}
						placeholder="e.g., unknown redirect URIs"
					/>
				</div>
			</div>
		</Dialog>
	);
}

/** Scope chips shown inline before the "+N more" affordance takes over. */
const SCOPE_PREVIEW_COUNT = 4;

/** Redirect URIs listed inline before collapsing behind a disclosure. */
const URI_INLINE_LIMIT = 2;

/**
 * One registration awaiting decision — GitHub's app-approval "review card"
 * grammar, origins-first:
 *
 *  - HEADER: the verifiable identity leads (redirect-URI origins as the
 *    heading, with the type/status chips beside it) and provenance
 *    ("registered <timeAgo>") sits right-aligned; the attacker-chosen name
 *    is demoted to a quiet, explicitly labelled "Self-reported name" line.
 *  - BODY: compact muted metadata — client_id (+ copy), the full redirect
 *    URIs (behind a disclosure when long; the heading's origins already
 *    summarise them), and a one-line scope summary with a "+N more"
 *    expander instead of a chip wall (13 chips must not out-shout the
 *    decision).
 *  - FOOTER: the decision verbs, grouped and right-anchored — Deny (quiet,
 *    danger-tinted) before Approve (primary). Denied rows re-offer Approve
 *    only (the deliberate denied→active recovery).
 */
function QueueRow({
	client,
	onApprove,
	onDeny,
	approvePending,
	denyPending,
}: {
	client: OAuthClient;
	onApprove: (client: OAuthClient) => void;
	onDeny: (client: OAuthClient) => void;
	approvePending: boolean;
	denyPending: boolean;
}) {
	const origins = clientOrigins(client);
	const [scopesExpanded, setScopesExpanded] = useState(false);
	const [urisExpanded, setUrisExpanded] = useState(false);

	const scopes = client.allowed_scopes;
	const visibleScopes =
		scopes == null ? [] : scopesExpanded ? scopes : scopes.slice(0, SCOPE_PREVIEW_COUNT);
	const hiddenScopeCount = scopes == null ? 0 : scopes.length - visibleScopes.length;
	const showUriList = client.redirect_uris.length <= URI_INLINE_LIMIT || urisExpanded;

	return (
		<article className="border-border rounded-lg border">
			{/* Header: the VERIFIABLE identity leads; provenance right-aligned. */}
			<div className="px-4 pt-4">
				<div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1">
					<h3 className="text-foreground flex min-w-0 flex-wrap items-center gap-2 font-medium">
						<code className="min-w-0 truncate font-mono text-sm">
							{origins.length > 0 ? origins.join(', ') : '(no redirect URIs)'}
						</code>
						<ClientTypeChips client={client} />
						<ClientStatusBadges client={client} />
					</h3>
					<p className="text-muted-foreground shrink-0 text-xs">
						registered{' '}
						<span title={formatTimestamp(client.created_at)}>
							{timeAgo(client.created_at)}
						</span>
						{client.registration_source === 'admin' && client.created_by && (
							<>
								{' '}
								by <ActorLabel actorId={client.created_by} />
							</>
						)}
					</p>
				</div>
				{/* The self-chosen display name — quiet, and explicitly marked. */}
				<p className="text-muted-foreground mt-1 text-sm">
					Self-reported name: <span className="text-foreground">{client.name}</span>
					{client.software_id && (
						<code className="ml-2 font-mono text-xs">{client.software_id}</code>
					)}
					{client.description && <span> — {client.description}</span>}
				</p>
			</div>

			{/* Quiet metadata block — mono/muted, not competing with the title. */}
			<div className="text-muted-foreground space-y-1.5 px-4 pt-3 pb-4 text-xs">
				<p className="flex items-center gap-1">
					<code className="bg-muted rounded px-1.5 py-0.5 font-mono">
						{client.client_id}
					</code>
					<CopyButton
						value={client.client_id}
						variant="ghost"
						size="icon"
						className="h-5 w-5 p-0.5"
						toastMessage="Client ID copied"
						ariaLabel={`Copy client ID for ${client.name}`}
					/>
				</p>
				{/* The heading's origins already summarise the URIs; the full
				    list collapses behind a disclosure when it gets long. */}
				{client.redirect_uris.length > URI_INLINE_LIMIT && (
					<Button
						variant="ghost"
						size="sm"
						className="h-auto px-1 py-0.5 text-xs"
						aria-expanded={urisExpanded}
						onClick={(): void => setUrisExpanded((v) => !v)}
					>
						{urisExpanded
							? 'Hide redirect URIs'
							: `Show ${client.redirect_uris.length} redirect URIs`}
					</Button>
				)}
				{showUriList && (
					<ul className="space-y-0.5">
						{client.redirect_uris.map((uri) => (
							<li key={uri} className="truncate font-mono">
								{uri}
							</li>
						))}
					</ul>
				)}
				{scopes != null && (
					<p className="flex flex-wrap items-center gap-1">
						<span>Allowed scopes:</span>
						{scopes.length === 0 ? (
							<span className="text-foreground">OIDC only</span>
						) : (
							visibleScopes.map((scope) => (
								<Badge key={scope} variant="default">
									{scope}
								</Badge>
							))
						)}
						{hiddenScopeCount > 0 && (
							<Button
								variant="ghost"
								size="sm"
								className="h-auto px-1 py-0.5 text-xs"
								aria-expanded={false}
								onClick={(): void => setScopesExpanded(true)}
							>
								+{hiddenScopeCount} more
							</Button>
						)}
						{scopesExpanded && scopes.length > SCOPE_PREVIEW_COUNT && (
							<Button
								variant="ghost"
								size="sm"
								className="h-auto px-1 py-0.5 text-xs"
								aria-expanded
								onClick={(): void => setScopesExpanded(false)}
							>
								Show less
							</Button>
						)}
					</p>
				)}
			</div>

			{/* Footer: the decision, grouped and anchored — never floating in
			    the header where it competed with the identity signal. */}
			<footer className="border-border flex flex-wrap items-center justify-end gap-2 border-t px-4 py-3">
				{/* A denied row is already denied — the only verb it re-offers
				    is Approve (the deliberate denied→active recovery). */}
				{client.approval_status !== 'denied' && (
					<Button
						size="sm"
						variant="ghost"
						className="text-danger hover:text-danger"
						onClick={(): void => onDeny(client)}
						disabled={denyPending}
					>
						{denyPending ? 'Denying…' : 'Deny'}
					</Button>
				)}
				<Button size="sm" onClick={(): void => onApprove(client)} disabled={approvePending}>
					{approvePending ? 'Approving…' : 'Approve'}
				</Button>
			</footer>
		</article>
	);
}

interface ApprovalQueueProps {
	/**
	 * Controlled filter — lifted to the section so a denied roster row's
	 * "Review in queue" action can land directly on the Denied slice.
	 */
	filter: QueueFilter;
	onFilterChange: (filter: QueueFilter) => void;
}

/**
 * The DCR approval queue (D7): registrations land `pending` + inactive;
 * Approve activates them, Deny keeps the row (reversible — the Denied filter
 * re-offers Approve as the recovery path). Approval-first is the default
 * everywhere (D7 as amended 2026-09-03): every fresh instance's first DCR
 * client lands here. With the explicit `auto_approve_clients: true` opt-in
 * the queue is normally empty.
 */
export function ApprovalQueue({ filter, onFilterChange }: ApprovalQueueProps) {
	const { data: clients, isLoading, error } = useOAuthClientQueue(filter);
	const approveMutation = useApproveOAuthClient();
	const denyMutation = useDenyOAuthClient();
	// Target + open are separate so a casual dismiss keeps the target (and the
	// dialog's reason draft — dialog-state rule); only a committed deny clears it.
	const [denyTarget, setDenyTarget] = useState<OAuthClient | null>(null);
	const [denyOpen, setDenyOpen] = useState(false);

	const handleApprove = async (client: OAuthClient): Promise<void> => {
		try {
			await approveMutation.mutateAsync(client.id);
			toast({ title: `${client.name} approved`, variant: 'success' });
		} catch (err) {
			toast({
				title: 'Failed to approve client',
				description: err instanceof Error ? err.message : undefined,
				variant: 'error',
			});
		}
	};

	const handleDeny = async (reason: string): Promise<void> => {
		if (!denyTarget) return;
		try {
			await denyMutation.mutateAsync({
				id: denyTarget.id,
				reason: reason || undefined,
			});
			toast({ title: `${denyTarget.name} denied`, variant: 'success' });
			setDenyOpen(false);
			// Clearing the target resets the dialog's reason draft (commit path).
			setDenyTarget(null);
		} catch (err) {
			toast({
				title: 'Failed to deny client',
				description: err instanceof Error ? err.message : undefined,
				variant: 'error',
			});
		}
	};

	return (
		<div className="space-y-4">
			<SegmentedToggle
				options={QUEUE_FILTER_OPTIONS}
				value={filter}
				onChange={onFilterChange}
				ariaLabel="Filter queue by decision"
			/>

			{isLoading ? (
				<LoadingState message="Loading approval queue..." />
			) : error ? (
				<ErrorAlert message="Failed to load the approval queue" />
			) : !clients?.length ? (
				<EmptyState
					icon={<CheckCircle2 className="h-6 w-6" />}
					title={filter === 'pending' ? 'No pending registrations' : 'No denied clients'}
					description={
						filter === 'pending'
							? 'Client registrations awaiting approval will appear here.'
							: 'Denied registrations are kept here — approving one reverses the decision.'
					}
				/>
			) : (
				<div className="space-y-4">
					{clients.map((client) => (
						<QueueRow
							key={client.id}
							client={client}
							onApprove={(c): void => void handleApprove(c)}
							onDeny={(c): void => {
								setDenyTarget(c);
								setDenyOpen(true);
							}}
							// Per-row pending (mutation variables carry the target id)
							// so one in-flight decision doesn't grey out the whole queue.
							approvePending={
								approveMutation.isPending && approveMutation.variables === client.id
							}
							denyPending={
								denyMutation.isPending && denyMutation.variables?.id === client.id
							}
						/>
					))}
				</div>
			)}

			{/* Mounted persistently (not `{target && …}`) so a casual dismiss
			    keeps the reason draft — see DenyClientDialog. */}
			<DenyClientDialog
				client={denyTarget}
				open={denyOpen}
				onClose={(): void => setDenyOpen(false)}
				onConfirm={(reason): void => void handleDeny(reason)}
				isPending={denyMutation.isPending}
			/>
		</div>
	);
}
