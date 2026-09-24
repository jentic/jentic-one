/**
 * ApiSetupQueue — step 2 of the Add-APIs flow: finish the batch the tray handed
 * over, one API at a time.
 *
 * There is no `Skip for now`, so every API that leaves here has a credential
 * bound: reuse binds without a pane, the only alternative to finishing is
 * dropping, and closing hands the remainder back to the host. Bindings are created
 * with no rules — default-deny, so an added API cannot serve traffic yet.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { AlertTriangle, Check, KeyRound, Loader2, LogIn, Minus, X } from 'lucide-react';
import { Badge, Button, SheetPrimitive } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import { useImportCatalogEntry, type Credential } from '@/shared/credentials/api';
import { useDeviceAwareConnect } from '@/shared/credentials/components/useDeviceAwareConnect';
import {
	CreateCredentialFlow,
	type CreatedCredentialInfo,
} from '@/shared/credentials/components/CreateCredentialFlow';
import { useBindAgentCredential } from '@/modules/agents/api';
import {
	credentialAwaitsConsent,
	type CredentialChoice,
} from '@/shared/credentials/lib/credentialIdentity';
import { CredentialOptions } from '@/shared/credentials/components/CredentialOptions';
import { currentChoice, type PreflightItem } from '@/modules/agents/lib/apiPreflight';
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
	/** Close, handing back the items that never reached a terminal state — the host
	 * must keep them, since the queue is the only way an API arrives. */
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
	/** Entry key → the credential choice the operator made in its pane. Absent =
	 * whatever the tray settled on ({@link currentChoice}). */
	const [choices, setChoices] = useState<Record<string, CredentialChoice>>({});

	// A new batch replaces the queue outright. Compared by reference: a content
	// compare would fight the in-progress statuses.
	const lastItemsRef = useRef(items);
	useEffect(() => {
		if (lastItemsRef.current === items) return;
		lastItemsRef.current = items;
		setEntries(buildQueue(items));
		setDropKey(null);
		setFormKey(null);
		setChoices({});
	}, [items]);

	// Silent: every outcome is reported on its own row, so a batch of five would
	// otherwise fire five toasts.
	const bindMutation = useBindAgentCredential(agentId, { silent: true });
	const importMutation = useImportCatalogEntry();
	const { connect: runConnect, deviceDialog } = useDeviceAwareConnect();

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

	/** Take one item to its terminal state: import the API if needed, bind the
	 * credential with no rules, then run the consent flow if sign-in is outstanding.
	 * An unfinished sign-in does NOT undo the bind — the tile renders dashed until
	 * the credential can serve. */
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
			const outcome = await runConnect(credentialId, credential.name).catch(
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
		// `settle` is recreated every render; the key and status decide whether it runs.
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [open, active?.key, active?.status]);

	const drop = (entry: QueueEntry): void => {
		setEntries((current) => patchEntry(current, entry.key, { status: 'dropped' }));
		setDropKey(null);
	};

	/** Bind a credential the wizard just created. The wizard already imported an
	 * unregistered catalog API on save. */
	const settleCreated = (
		entry: QueueEntry,
		created: NonNullable<QueueEntry['created']>,
	): Promise<void> =>
		settle(entry, created, { connect: created.needsConnect, alreadyImported: true });

	const handleCreated = (entry: QueueEntry, info: CreatedCredentialInfo): void => {
		setFormKey(null);
		const created = {
			credential_id: info.credentialId,
			name: info.name,
			needsConnect: info.needsConnect,
		};
		setEntries((current) => patchEntry(current, entry.key, { created }));
		void settleCreated(entry, created);
	};

	const retry = (key: string): void => {
		const entry = entries.find((e) => e.key === key);
		if (entry?.created) void settleCreated(entry, entry.created);
		else setEntries((current) => retryEntry(current, key));
	};

	// A bind in flight has to land before the queue can hand its item back —
	// reopening on it would bind the same credential again.
	const busy = entries.some((e) => e.status === 'working');
	const close = (): void => onClose(unfinishedItems(entries));
	// The wizard stacks as a second SheetPrimitive and both see the same Escape —
	// dismissing it must leave the operator in the queue.
	const guardedClose = (): void => {
		if (formKey != null || busy) return;
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
						disabled={busy}
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
							selected={choices[active.key] ?? currentChoice(active)}
							onSelect={(next): void =>
								setChoices((current) => ({ ...current, [active.key]: next }))
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
						onRetry={retry}
					/>
				</div>

				<footer className="border-border bg-card/60 border-t">
					<div className="bg-muted h-0.5 w-full" aria-hidden="true">
						<div
							className="bg-primary h-full transition-[width] duration-300 ease-out"
							style={{
								width: `${summary.total === 0 ? 0 : ((summary.total - summary.remaining) / summary.total) * 100}%`,
							}}
						/>
					</div>
					<div className="space-y-1.5 px-5 py-3">
						<div className="flex items-center justify-between gap-3">
							{/* Progress, not the outcome — the outcome is the done pane's
							    job, and saying it twice makes neither authoritative. */}
							<p
								className="text-foreground text-xs font-medium tabular-nums"
								role="status"
							>
								{summary.total - summary.remaining} of {summary.total} done
							</p>
							<Button
								size="sm"
								variant={summary.done ? 'primary' : 'outline'}
								onClick={close}
								disabled={busy}
								className="shrink-0"
							>
								{summary.done ? 'Done' : 'Close for now'}
							</Button>
						</div>
						{/* One note, not a stack: what an added API can do yet, and — while anything
						    is outstanding, failures included — what closing costs. */}
						<p className="text-muted-foreground/90 text-xs leading-snug">
							{QUEUE_RULES_NOTICE}
							{!summary.done && (
								<>
									{' '}
									Closing keeps the APIs already added; the remaining{' '}
									{summary.remaining} wait here for next time.
								</>
							)}
						</p>
					</div>
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
			{deviceDialog}
		</SheetPrimitive>
	);
}

/** The pane for the item at the front of the queue.
 *
 * An API that existing credentials cover shows them all as cards, with "Add a new
 * credential" last and the tray's choice preselected — reuse matches API identity,
 * not account, so a wrong-tenant match must be rejectable without dropping the
 * API. The primary action follows the selected card. */
function ActivePane({
	entry,
	agentName,
	dropPending,
	selected,
	onSelect,
	onUseCredential,
	onOpenForm,
	onAskDrop,
	onCancelDrop,
	onConfirmDrop,
}: {
	entry: QueueEntry;
	agentName: string;
	dropPending: boolean;
	selected: CredentialChoice | null;
	onSelect: (choice: CredentialChoice) => void;
	onUseCredential: (credential: Credential) => void;
	onOpenForm: () => void;
	onAskDrop: () => void;
	onCancelDrop: () => void;
	onConfirmDrop: () => void;
}) {
	const working = entry.status === 'working';
	const count = entry.covering.length;
	const existing =
		selected?.kind === 'existing'
			? (entry.covering.find((c) => c.credential_id === selected.credentialId) ?? null)
			: null;
	const wantsNew = count === 0 || selected?.kind === 'new';
	/** A new credential for this API is one sign-in click — the tray established it. */
	const newIsSignIn = entry.outcome === 'oauth' && entry.candidates.length === 0;

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

			{count > 0 && (
				<CredentialOptions
					id={`queue-credential-${entry.key}`}
					legend={
						count === 1
							? `You have 1 credential for ${entry.api.label}. Should ${agentName} use it, or a new one?`
							: `You have ${count} credentials for ${entry.api.label}. Which should ${agentName} use?`
					}
					credentials={entry.covering}
					selected={selected}
					onSelect={onSelect}
					newCredentialDetail="Another key or account for this API — nothing is stored until you save it"
					disabled={working || dropPending}
				/>
			)}

			{existing && credentialAwaitsConsent(existing) && (
				<p className="text-muted-foreground text-xs">
					<span className="text-foreground font-medium">{existing.name}</span> is ready to
					use — it just needs you to finish signing in.
				</p>
			)}

			{count === 0 && (
				<p className="text-muted-foreground text-xs">
					{newIsSignIn
						? 'Sign in once and this API is set up — there is nothing to type in.'
						: 'This API needs a new credential. Nothing is stored until you save it.'}
				</p>
			)}

			{dropPending ? (
				<div
					className="border-warning/40 bg-warning/5 space-y-2 rounded-lg border p-3"
					data-testid="queue-drop-confirm"
				>
					<p className="text-foreground flex items-start gap-2 text-xs">
						<AlertTriangle className="text-warning mt-0.5 h-3.5 w-3.5 shrink-0" />
						{/* There is no "later" state to drop into. */}
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
					{existing ? (
						<Button
							size="sm"
							disabled={working}
							onClick={(): void => onUseCredential(existing)}
						>
							{credentialAwaitsConsent(existing) ? (
								<>
									<LogIn className="h-4 w-4" />
									Sign in to {entry.api.label}
								</>
							) : (
								<>
									<Check className="h-4 w-4" />
									Use this credential
								</>
							)}
						</Button>
					) : wantsNew ? (
						<Button size="sm" disabled={working} onClick={onOpenForm}>
							<KeyRound className="h-4 w-4" />
							Add credential
						</Button>
					) : (
						// Nothing selected yet, which only happens among several: the
						// button waits for a card rather than guessing one.
						<Button size="sm" disabled>
							<Check className="h-4 w-4" />
							Use this credential
						</Button>
					)}
					<Button size="sm" variant="ghost" disabled={working} onClick={onAskDrop}>
						Not this one
					</Button>
				</div>
			)}
		</section>
	);
}

/** The outcome of the batch. Everything dropped or failed is not a success — no
 * success mark, no "now set the rules" follow-up. */
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
