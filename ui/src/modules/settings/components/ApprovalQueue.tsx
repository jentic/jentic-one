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

/** One registration awaiting decision, origins-first. */
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
	return (
		<div className="border-border rounded-lg border p-4">
			<div className="flex items-start justify-between gap-3">
				<div className="min-w-0">
					{/* Headline = the VERIFIABLE identity: redirect-URI origins
					    (+ software_id), never the self-chosen display name. */}
					<h3 className="text-foreground flex flex-wrap items-center gap-2 font-medium">
						<code className="min-w-0 truncate font-mono text-sm">
							{origins.length > 0 ? origins.join(', ') : '(no redirect URIs)'}
						</code>
						<ClientTypeChips client={client} />
						<ClientStatusBadges client={client} />
					</h3>
					{client.software_id && (
						<p className="text-muted-foreground mt-0.5 font-mono text-xs">
							{client.software_id}
						</p>
					)}
					<p className="text-muted-foreground mt-1 text-sm">
						Self-reported name: <span className="text-foreground">{client.name}</span>
						{client.description && <span> — {client.description}</span>}
					</p>
				</div>
				<div className="flex shrink-0 gap-2">
					<Button
						size="sm"
						onClick={(): void => onApprove(client)}
						disabled={approvePending}
					>
						Approve
					</Button>
					{/* A denied row is already denied — the only verb it re-offers
					    is Approve (the deliberate denied→active recovery). */}
					{client.approval_status !== 'denied' && (
						<Button
							size="sm"
							variant="outline"
							onClick={(): void => onDeny(client)}
							disabled={denyPending}
						>
							Deny
						</Button>
					)}
				</div>
			</div>
			<div className="mt-3 space-y-2 text-sm">
				<div>
					<span className="text-muted-foreground">Client ID: </span>
					<code className="bg-muted rounded px-1.5 py-0.5 font-mono text-xs">
						{client.client_id}
					</code>
				</div>
				<div>
					<span className="text-muted-foreground">Redirect URIs: </span>
					<ul className="text-foreground mt-1 list-inside list-disc pl-1">
						{client.redirect_uris.map((uri) => (
							<li key={uri} className="truncate font-mono text-xs">
								{uri}
							</li>
						))}
					</ul>
				</div>
				{client.allowed_scopes != null && (
					<div className="flex flex-wrap items-center gap-1">
						<span className="text-muted-foreground">Allowed scopes: </span>
						{client.allowed_scopes.length > 0 ? (
							client.allowed_scopes.map((scope) => (
								<Badge key={scope} variant="default">
									{scope}
								</Badge>
							))
						) : (
							<span className="text-foreground text-xs">OIDC only</span>
						)}
					</div>
				)}
				<p className="text-muted-foreground text-xs">
					Registered{' '}
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
		</div>
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
