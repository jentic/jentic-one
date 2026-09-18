/**
 * ApiSetupQueue — step 2 of the Add-APIs flow (plan §4.4): finish the batch the
 * tray handed over, one API at a time.
 *
 * Every API that reaches an agent leaves this queue with a credential bound to
 * it, because there is no `Skip for now` (D13). That single rule shapes the
 * whole component:
 *
 *  - **Reuse is free.** Items whose credential already exists are bound without
 *    ever showing a pane, first, before anything asks for attention.
 *  - **Dropping is not deferring.** The only alternative to finishing an item is
 *    dropping it, and the confirm says what that means: the API is not added.
 *  - **Per-item terminal state.** Binding a batch is N sequential POSTs, so one
 *    failure must not take the others with it. Each row carries its own outcome
 *    and its own retry.
 *  - **Re-enterable.** Closing mid-way keeps whatever completed and hands the
 *    remainder back to the host, which reopens the flow on it. Nothing is lost
 *    silently, because there is no deferred state for it to be lost into.
 *
 * Bindings are created with no rules — least privilege (C1), which is the
 * broker's default-deny state. The footer says so: an added API cannot serve
 * traffic until rules exist on it.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { AlertTriangle, Check, KeyRound, Loader2, LogIn, Minus, X } from 'lucide-react';
import { Badge, Button, SheetPrimitive } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import {
	useImportCatalogEntry,
	useRunConnectFlow,
	type Credential,
} from '@/shared/credentials/api';
import {
	CreateCredentialFlow,
	type CreatedCredentialInfo,
} from '@/shared/credentials/components/CreateCredentialFlow';
import { CredentialTypeBadge } from '@/shared/credentials/components/CredentialTypeBadge';
import { useBindAgentCredential } from '@/modules/agents/api';
import { credentialAwaitsConsent } from '@/modules/agents/lib/apiTiles';
import type { PreflightItem } from '@/modules/agents/lib/apiPreflight';
import {
	QUEUE_RULES_NOTICE,
	QUEUE_STATUS_LABELS,
	activeEntry,
	buildQueue,
	dropWarning,
	markActive,
	needsPane,
	patchEntry,
	queueSummary,
	queueSummaryLine,
	retryEntry,
	unfinishedItems,
	type QueueEntry,
	type QueueSummary,
} from '@/modules/agents/lib/setupQueue';

export interface ApiSetupQueueProps {
	open: boolean;
	/** The agent every credential in this batch is bound to. */
	agentId: string;
	agentName: string;
	/** The preflighted batch from the tray, in the order it should be worked. */
	items: PreflightItem[];
	/**
	 * Close the queue, handing back the items that never reached a terminal
	 * state. The host must keep them: the queue is the only way an API arrives,
	 * so an abandoned item has to be resumable rather than quietly dropped.
	 */
	onClose: (remaining: PreflightItem[]) => void;
}

function errorText(e: unknown): string {
	return e instanceof Error && e.message ? e.message : 'Something went wrong.';
}

export function ApiSetupQueue({ open, agentId, agentName, items, onClose }: ApiSetupQueueProps) {
	const headingId = 'api-setup-queue-title';
	const [entries, setEntries] = useState<QueueEntry[]>(() => buildQueue(items));
	/** The entry whose drop is awaiting confirmation. */
	const [dropKey, setDropKey] = useState<string | null>(null);
	/** The entry whose credential wizard is open. */
	const [formKey, setFormKey] = useState<string | null>(null);
	/** `choose` panes: entry key → the credential the operator selected. */
	const [choice, setChoice] = useState<Record<string, string>>({});

	// A new batch replaces the queue outright. Compared by reference, not by
	// content: the host hands over a fresh array per commit (including a resumed
	// remainder), and a content compare would fight the in-progress statuses.
	const lastItemsRef = useRef(items);
	useEffect(() => {
		if (lastItemsRef.current === items) return;
		lastItemsRef.current = items;
		setEntries(buildQueue(items));
		setDropKey(null);
		setFormKey(null);
	}, [items]);

	// Silent: the queue reports every outcome on its own rows, so a batch of
	// five would otherwise fire five toasts, and an error toast would compete
	// with the row's own `Try again`.
	const bindMutation = useBindAgentCredential(agentId, { silent: true });
	const importMutation = useImportCatalogEntry();
	const runConnect = useRunConnectFlow();

	const active = useMemo(() => activeEntry(entries), [entries]);
	const summary = useMemo(() => queueSummary(entries), [entries]);
	const formEntry = useMemo(
		() => entries.find((e) => e.key === formKey) ?? null,
		[entries, formKey],
	);

	// Keep the live row marked so the pane and the progress list never disagree
	// about which API is being worked on.
	useEffect(() => {
		setEntries((current) => markActive(current));
	}, [entries]);

	/**
	 * Take one item to its terminal state: import the API if accepting it means
	 * importing (D5), bind the credential with no rules, then — for a credential
	 * whose sign-in is still outstanding — run the consent flow.
	 *
	 * An unfinished sign-in does NOT undo the bind. The binding is real, and the
	 * surface already tells that truth: the API tile renders dashed until the
	 * credential can actually serve (D14). Discarding here would instead leave
	 * the operator with an API they were told was added and no way to see why it
	 * is not.
	 */
	const inFlight = useRef<Set<string>>(new Set());
	const settle = async (
		entry: QueueEntry,
		/** The credential to bind — carried whole so the finished row can name it. */
		credential: { credential_id: string; name: string },
		opts: { connect?: boolean; alreadyImported?: boolean } = {},
	): Promise<void> => {
		const credentialId = credential.credential_id;
		if (inFlight.current.has(entry.key)) return;
		inFlight.current.add(entry.key);
		setEntries((current) =>
			patchEntry(current, entry.key, { status: 'working', error: undefined }),
		);
		try {
			if (entry.importsApi && entry.api.apiId && !opts.alreadyImported) {
				await importMutation.mutateAsync(entry.api.apiId);
			}
			await bindMutation.mutateAsync({ credentialId, rules: null });
		} catch (e) {
			setEntries((current) =>
				patchEntry(current, entry.key, { status: 'failed', error: errorText(e) }),
			);
			inFlight.current.delete(entry.key);
			return;
		}

		let note: string | undefined;
		if (opts.connect) {
			const outcome = await runConnect(credentialId).catch(
				() => ({ status: 'cancelled' }) as const,
			);
			if (outcome.status !== 'connected' && outcome.status !== 'redirected') {
				note = 'Sign-in not finished — the API is added but cannot be called yet.';
			}
		}
		setEntries((current) =>
			patchEntry(current, entry.key, {
				status: 'added',
				credentialId,
				credentialName: credential.name,
				note,
			}),
		);
		inFlight.current.delete(entry.key);
	};

	// `reuse` bypasses the pane: bind it as soon as it reaches the front.
	useEffect(() => {
		if (!open || !active || needsPane(active.outcome)) return;
		if (active.status === 'working') return;
		const credential = active.candidates[0];
		if (!credential) {
			setEntries((current) =>
				patchEntry(current, active.key, {
					status: 'failed',
					error: 'The credential this API was going to reuse is no longer available.',
				}),
			);
			return;
		}
		void settle(active, credential);
		// `settle` is recreated every render; the item's key and status are what
		// decide whether it runs.
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [open, active?.key, active?.status]);

	const drop = (entry: QueueEntry): void => {
		setEntries((current) => patchEntry(current, entry.key, { status: 'dropped' }));
		setDropKey(null);
	};

	const handleCreated = (entry: QueueEntry, info: CreatedCredentialInfo): void => {
		setFormKey(null);
		// The wizard already imported an unregistered catalog API on save.
		void settle(
			entry,
			{ credential_id: info.credentialId, name: info.name },
			{ connect: info.needsConnect, alreadyImported: true },
		);
	};

	const close = (): void => onClose(unfinishedItems(entries));
	// The credential wizard stacks as a second SheetPrimitive over this one, and
	// both see the same Escape keydown — dismissing the wizard must leave the
	// operator in the queue it was opened from, with the batch intact.
	const guardedClose = (): void => {
		if (formKey != null) return;
		close();
	};

	return (
		<SheetPrimitive
			open={open}
			onClose={guardedClose}
			ariaLabelledBy={headingId}
			className="sm:w-[560px] xl:w-[640px]"
		>
			<div className="flex h-full flex-col">
				<header className="border-border flex items-start justify-between gap-3 border-b px-5 py-4">
					<div className="min-w-0">
						<h2 id={headingId} className="text-foreground text-base font-semibold">
							Set up {summary.total} {summary.total === 1 ? 'API' : 'APIs'}
						</h2>
						<p className="text-muted-foreground text-xs">
							Each one gets a credential before {agentName} can call it.
						</p>
					</div>
					<Button
						variant="ghost"
						size="sm"
						aria-label="Close"
						onClick={close}
						className="text-muted-foreground hover:text-foreground shrink-0"
					>
						<X className="h-4 w-4" />
					</Button>
				</header>

				<div className="flex-1 space-y-4 overflow-y-auto px-5 py-4">
					{active ? (
						<ActivePane
							entry={active}
							agentName={agentName}
							dropPending={dropKey === active.key}
							selectedCredentialId={choice[active.key] ?? null}
							onSelectCredential={(credentialId): void =>
								setChoice((c) => ({ ...c, [active.key]: credentialId }))
							}
							onUseCredential={(credential): void => {
								void settle(active, credential, {
									// A credential whose first sign-in never completed
									// still needs that click before it can serve.
									connect: credentialAwaitsConsent(credential),
								});
							}}
							onOpenForm={(): void => setFormKey(active.key)}
							onAskDrop={(): void => setDropKey(active.key)}
							onCancelDrop={(): void => setDropKey(null)}
							onConfirmDrop={(): void => drop(active)}
						/>
					) : (
						<DonePane summary={summary} />
					)}

					<ProgressList
						entries={entries}
						activeKey={active?.key ?? null}
						onRetry={(key): void => setEntries((current) => retryEntry(current, key))}
					/>
				</div>

				<footer className="border-border space-y-2 border-t px-5 py-3">
					<p className="text-muted-foreground/90 text-xs">{QUEUE_RULES_NOTICE}</p>
					<div className="flex items-center justify-between gap-3">
						{/* Progress, not the outcome — the outcome is the done pane's
						    job, and saying it twice makes neither authoritative. */}
						<p className="text-muted-foreground text-xs" role="status">
							{summary.total - summary.unfinished} of {summary.total} done
						</p>
						<Button
							size="sm"
							variant={summary.done ? 'primary' : 'ghost'}
							onClick={close}
						>
							{summary.done ? 'Done' : 'Close for now'}
						</Button>
					</div>
					{!summary.done && (
						// Say what closing costs, so it is a choice rather than a
						// surprise: finished APIs stay, the rest is still owed.
						<p className="text-muted-foreground/80 text-xs">
							Closing keeps the APIs already added. The remaining {summary.unfinished}{' '}
							will be waiting next time you add APIs.
						</p>
					)}
				</footer>
			</div>

			{formEntry && (
				<CreateCredentialFlow
					key={formEntry.key}
					open
					pinnedApi={formEntry.api}
					onClose={(): void => setFormKey(null)}
					onCreated={(info): void => handleCreated(formEntry, info)}
				/>
			)}
		</SheetPrimitive>
	);
}

/** The pane for the item at the front of the queue. */
function ActivePane({
	entry,
	agentName,
	dropPending,
	selectedCredentialId,
	onSelectCredential,
	onUseCredential,
	onOpenForm,
	onAskDrop,
	onCancelDrop,
	onConfirmDrop,
}: {
	entry: QueueEntry;
	agentName: string;
	dropPending: boolean;
	selectedCredentialId: string | null;
	onSelectCredential: (credentialId: string) => void;
	onUseCredential: (credential: Credential) => void;
	onOpenForm: () => void;
	onAskDrop: () => void;
	onCancelDrop: () => void;
	onConfirmDrop: () => void;
}) {
	const working = entry.status === 'working';
	const signInCandidate = entry.outcome === 'oauth' ? (entry.candidates[0] ?? null) : null;
	/** The pane is offering an existing credential, so it owes an escape from it. */
	const hasCandidates = entry.candidates.length > 0;
	const chosen =
		entry.outcome === 'choose'
			? (entry.candidates.find((c) => c.credential_id === selectedCredentialId) ?? null)
			: null;

	return (
		<section
			aria-label={`Set up ${entry.api.label}`}
			data-testid="queue-active-pane"
			className="border-border bg-muted/20 space-y-3 rounded-xl border p-4"
		>
			<div className="flex items-start gap-3">
				<span
					aria-hidden
					className="bg-background border-border flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border"
				>
					<KeyRound className="text-muted-foreground h-4 w-4" />
				</span>
				<div className="min-w-0 flex-1">
					<p className="text-foreground truncate text-sm font-medium">
						{entry.api.label}
					</p>
					<p className="text-muted-foreground font-mono text-xs">
						{entry.api.vendor}/{entry.api.name}
					</p>
				</div>
				{working && (
					<span className="text-muted-foreground inline-flex shrink-0 items-center gap-1.5 text-xs">
						<Loader2 className="h-3.5 w-3.5 animate-spin" />
						Adding…
					</span>
				)}
			</div>

			{entry.outcome === 'choose' && (
				<fieldset className="space-y-2" disabled={working}>
					<legend className="text-muted-foreground mb-1 text-xs">
						{entry.candidates.length} credentials can reach this API. Which one should{' '}
						{agentName} use?
					</legend>
					{entry.candidates.map((candidate) => (
						<label
							key={candidate.credential_id}
							className={cn(
								'border-border hover:bg-muted/40 flex cursor-pointer items-center gap-3 rounded-lg border px-3 py-2',
								selectedCredentialId === candidate.credential_id &&
									'border-primary/60 bg-primary/5',
							)}
						>
							<input
								type="radio"
								name={`credential-${entry.key}`}
								className="accent-primary h-4 w-4 shrink-0"
								checked={selectedCredentialId === candidate.credential_id}
								onChange={(): void => onSelectCredential(candidate.credential_id)}
							/>
							<span className="text-foreground min-w-0 flex-1 truncate text-sm">
								{candidate.name}
							</span>
							<CredentialTypeBadge type={candidate.type} />
						</label>
					))}
				</fieldset>
			)}

			{entry.outcome === 'oauth' && signInCandidate && (
				<p className="text-muted-foreground text-xs">
					<span className="text-foreground font-medium">{signInCandidate.name}</span> is
					ready to use — it just needs you to finish signing in.
				</p>
			)}

			{entry.outcome === 'oauth' && !signInCandidate && (
				<p className="text-muted-foreground text-xs">
					Sign in once and this API is set up — there is nothing to type in.
				</p>
			)}

			{entry.outcome === 'form' && (
				<p className="text-muted-foreground text-xs">
					This API needs a new credential. Nothing is stored until you save it.
				</p>
			)}

			{dropPending ? (
				<div
					className="border-warning/40 bg-warning/5 space-y-2 rounded-lg border p-3"
					data-testid="queue-drop-confirm"
				>
					<p className="text-foreground flex items-start gap-2 text-xs">
						<AlertTriangle className="text-warning mt-0.5 h-3.5 w-3.5 shrink-0" />
						{/* D13: there is no "later" state to drop into. */}
						<span>
							{dropWarning(entry.api.label)} {agentName} will not be able to call it,
							and you can add it again whenever you like.
						</span>
					</p>
					<div className="flex items-center gap-2">
						<Button size="sm" variant="secondary" onClick={onConfirmDrop}>
							Drop {entry.api.label}
						</Button>
						<Button size="sm" variant="ghost" onClick={onCancelDrop}>
							Keep it
						</Button>
					</div>
				</div>
			) : (
				<div className="flex flex-wrap items-center gap-2">
					{entry.outcome === 'choose' ? (
						<Button
							size="sm"
							disabled={working || !chosen}
							onClick={(): void => {
								if (chosen) onUseCredential(chosen);
							}}
						>
							Use this credential
						</Button>
					) : signInCandidate ? (
						<Button
							size="sm"
							disabled={working}
							onClick={(): void => onUseCredential(signInCandidate)}
						>
							<LogIn className="h-4 w-4" />
							Sign in to {entry.api.label}
						</Button>
					) : (
						<Button size="sm" disabled={working} onClick={onOpenForm}>
							<KeyRound className="h-4 w-4" />
							Add credential
						</Button>
					)}
					{/* The escape from a matched credential. Reuse is a match on API
					    identity, not on account: an operator whose match is the wrong
					    tenant, environment, or person has to be able to say so without
					    dropping the API (D13 leaves no third option). */}
					{hasCandidates && (
						<Button size="sm" variant="ghost" disabled={working} onClick={onOpenForm}>
							Use a different credential
						</Button>
					)}
					<Button size="sm" variant="ghost" disabled={working} onClick={onAskDrop}>
						Not this one
					</Button>
				</div>
			)}

			{hasCandidates && !dropPending && (
				// Name what the escape is escaping from, so "different" is a
				// comparison the operator can actually make.
				<p className="text-muted-foreground/80 text-xs">
					{entry.outcome === 'choose'
						? `A new credential is an option too, if none of these ${entry.candidates.length} should be used for ${entry.api.label}.`
						: `${entry.candidates[0]?.name} is the only credential you have for ${entry.api.label} — add a new one if it is the wrong account.`}
				</p>
			)}
		</section>
	);
}

/**
 * The outcome of the batch. A batch where everything was dropped or failed is
 * not a success, so it does not get the success mark or the "now set the rules"
 * follow-up — there is nothing to set rules on.
 */
function DonePane({ summary }: { summary: QueueSummary }) {
	const nothingAdded = summary.added === 0;
	return (
		<section
			aria-label="Setup finished"
			data-testid="queue-done-pane"
			className="border-border bg-muted/20 flex items-start gap-3 rounded-xl border p-4"
		>
			<span
				aria-hidden
				className={cn(
					'flex h-9 w-9 shrink-0 items-center justify-center rounded-lg',
					nothingAdded ? 'bg-muted' : 'bg-success/10',
				)}
			>
				{nothingAdded ? (
					<Minus className="text-muted-foreground h-4 w-4" />
				) : (
					<Check className="text-success h-4 w-4" />
				)}
			</span>
			<div className="min-w-0">
				<p className="text-foreground text-sm font-medium">
					{nothingAdded ? 'Nothing was added.' : queueSummaryLine(summary)}
				</p>
				<p className="text-muted-foreground mt-0.5 text-xs">
					{nothingAdded
						? queueSummaryLine(summary) ||
							'This agent still cannot reach any of those APIs.'
						: 'Set the rules on each API tile to say what the agent may call.'}
				</p>
			</div>
		</section>
	);
}

const STATUS_STYLE: Record<QueueEntry['status'], string> = {
	waiting: 'text-muted-foreground',
	active: 'text-foreground',
	working: 'text-muted-foreground',
	added: 'text-success',
	dropped: 'text-muted-foreground',
	failed: 'text-destructive',
};

/** Every item and where it got to — the record a partial failure is read from. */
function ProgressList({
	entries,
	activeKey,
	onRetry,
}: {
	entries: QueueEntry[];
	activeKey: string | null;
	onRetry: (key: string) => void;
}) {
	return (
		<ol aria-label="Setup progress" className="space-y-1">
			{entries.map((entry) => (
				<li
					key={entry.key}
					data-testid="queue-progress-row"
					data-status={entry.status}
					className={cn(
						'flex flex-wrap items-center gap-2 rounded-lg px-2 py-1.5 text-sm',
						entry.key === activeKey && 'bg-muted/40',
					)}
				>
					<span
						className={cn(
							'min-w-0 flex-1 truncate',
							entry.status === 'dropped' && 'text-muted-foreground line-through',
						)}
					>
						{entry.api.label}
					</span>
					{entry.status === 'added' && (
						<Check className="text-success h-3.5 w-3.5 shrink-0" aria-hidden />
					)}
					{entry.status === 'dropped' && (
						<Minus className="text-muted-foreground h-3.5 w-3.5 shrink-0" aria-hidden />
					)}
					<span className={cn('shrink-0 text-xs', STATUS_STYLE[entry.status])}>
						{QUEUE_STATUS_LABELS[entry.status]}
					</span>
					{/* Which credential it went through. A `reuse` item never showed a
					    pane, so this row is the only place the choice is disclosed. */}
					{entry.status === 'added' && entry.credentialName && (
						<span className="text-muted-foreground shrink-0 text-xs">
							via {entry.credentialName}
						</span>
					)}
					{entry.status === 'failed' && (
						<>
							<Badge variant="danger" className="shrink-0 text-[10px]">
								{entry.error}
							</Badge>
							<Button
								size="sm"
								variant="ghost"
								onClick={(): void => onRetry(entry.key)}
								className="shrink-0"
							>
								Try again
							</Button>
						</>
					)}
					{entry.note && (
						<p className="text-muted-foreground w-full text-xs">{entry.note}</p>
					)}
				</li>
			))}
		</ol>
	);
}
