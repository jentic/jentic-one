/**
 * ApiSetupQueue — step 2 of the Add-APIs flow: finish the batch the tray handed
 * over, one API at a time, each in two steps — its credential, then its access.
 *
 * Credential: pick an existing credential or create one. Nothing is bound yet —
 * even a lone covering credential is only bound once the operator confirms the
 * whole item. Access: the same presets as the workspace bind (Allow all
 * operations · Read-only (GET only) · Custom rules, Custom preselected), with an
 * inline "Try a request" that dry-runs the rules as they stand — unsaved —
 * locally, with the broker's semantics. Confirming binds the credential, then
 * saves its rules, and only advances once both landed AND the binding surfaces
 * refetched — the agent's rows are never left reading a stale "Blocked". A rules
 * save that fails leaves the binding (blocked) and offers Retry in place, with
 * the operator's rules kept.
 *
 * "Set up later" adds the API with no rules: bound, but every call denied — the
 * row reads Blocked, truthfully. Dropping is the alternative to adding at all;
 * closing hands the remainder back to the host. `Back to APIs` returns to the
 * tray to edit the batch; the host keeps this component mounted meanwhile, and
 * the edited batch is folded back in ({@link reconcileQueue}) so progress — the
 * step each item is on, its credential and its access draft — survives the round
 * trip.
 *
 * An API the agent already reaches is set up the same way: the pane names the
 * credentials it has and offers only credentials not bound to it yet, so the item
 * adds another credential (with its own rules — rules are per binding) rather
 * than a 409.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import {
	AlertTriangle,
	ArrowLeft,
	Check,
	KeyRound,
	Loader2,
	LockOpen,
	Minus,
	ShieldCheck,
	X,
} from 'lucide-react';
import {
	Badge,
	Button,
	ErrorAlert,
	SheetBody,
	SheetFooter,
	SheetHeader,
	SheetPrimitive,
	ConfirmDialog,
	type PermissionRuleInput,
} from '@/shared/ui';
import { apiIdentityTuple } from '@/shared/lib';
import { cn } from '@/shared/lib/utils';
import { useCredential, useImportCatalogEntry, type Credential } from '@/shared/credentials/api';
import {
	useApplyBindingRules,
	useBindCredentialToAgents,
} from '@/shared/credentials/api/vendors-hooks';
import type { PermissionRule } from '@/shared/credentials/api/vendors-types';
import { AccessRulesStep } from '@/shared/credentials/components/AccessRulesStep';
import {
	rulesForPreset,
	type RulesPreset,
	type ScopeReach,
} from '@/shared/credentials/lib/accessPresets';
import { apiScopeReach } from '@/shared/credentials/lib/apiIdentity';
import { useDeviceAwareConnect } from '@/shared/credentials/components/useDeviceAwareConnect';
import type { ConnectedCredentialInfo } from '@/shared/credentials/components/VendorConnectFlow';
import {
	CreateCredentialFlow,
	type CreatedCredentialInfo,
} from '@/shared/credentials/components/CreateCredentialFlow';
import {
	credentialAwaitsConsent,
	type CredentialChoice,
} from '@/shared/credentials/lib/credentialIdentity';
import { CredentialOptions } from '@/shared/credentials/components/CredentialOptions';
import {
	addedViaLabel,
	anotherCredentialWarning,
	defaultChoice,
	type PreflightItem,
} from '@/modules/agents/lib/apiPreflight';
import {
	QUEUE_ACCESS_LABELS,
	QUEUE_RULES_NOTICE,
	QUEUE_STATUS_LABELS,
	activeEntry,
	backToCredential,
	blockedCount,
	buildQueue,
	chooseCredential,
	dropWarning,
	markActive,
	patchEntry,
	queueBackSeed,
	queueSummary,
	queueSummaryLine,
	reconcileQueue,
	retryEntry,
	settleBoundOnClose,
	unfinishedItems,
	type ChosenCredential,
	type QueueAccess,
	type QueueBackSeed,
	type QueueEntry,
	type QueueSummary,
} from '@/modules/agents/lib/setupQueue';
import { AgentNameText } from '@/modules/agents/components/AgentNameText';
import { usePrimeBindingPermissions } from '@/modules/agents/api';

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
	/** Go back to the tray to edit the batch. `remaining` is what {@link onClose}
	 * would hand back, for a host whose tray is then closed instead of continued.
	 * The next `items` this queue receives are folded into its progress. */
	onBack?: (seed: QueueBackSeed, remaining: PreflightItem[]) => void;
}

function errorText(e: unknown): string {
	return e instanceof Error && e.message ? e.message : 'Something went wrong.';
}

/** One item's access draft — the preset and the custom rules being written. */
interface AccessDraft {
	preset: RulesPreset | null;
	customRules: PermissionRule[];
}

/** Custom rules, preselected: the operator says what the agent may call. */
const DEFAULT_DRAFT: AccessDraft = { preset: 'custom', customRules: [] };

const RULES_SAVE_FAILED = "The access rules weren't saved.";

export function ApiSetupQueue({
	open,
	agentId,
	agentName,
	items,
	onClose,
	onBack,
}: ApiSetupQueueProps) {
	const headingId = 'api-setup-queue-title';
	const [entries, setEntries] = useState<QueueEntry[]>(() => buildQueue(items));
	/** The entry whose drop is awaiting confirmation. */
	const [dropKey, setDropKey] = useState<string | null>(null);
	/** The entry whose credential wizard is open. */
	const [formKey, setFormKey] = useState<string | null>(null);
	/** Entry key → the credential choice the operator made in its pane. Absent =
	 * the pane's starting selection ({@link defaultChoice}). */
	const [choices, setChoices] = useState<Record<string, CredentialChoice>>({});
	/** Entry key → its access draft. Absent = {@link DEFAULT_DRAFT}. Kept across
	 * Back (to the credential step, or to the tray) until the queue is replaced. */
	const [drafts, setDrafts] = useState<Record<string, AccessDraft>>({});

	/** Asking before Back throws away a typed-in credential. */
	const [discardOpen, setDiscardOpen] = useState(false);
	/** Set by Back: the next batch is the same one, edited in the tray, so it is
	 * folded into the progress rather than replacing it. */
	const returningRef = useRef(false);
	/** After a round trip, the pane — not the header's first button — takes focus. */
	const [focusPane, setFocusPane] = useState(false);
	const paneRef = useRef<HTMLElement>(null);

	// A new batch replaces the queue outright. Compared by reference: a content
	// compare would fight the in-progress statuses.
	const lastItemsRef = useRef(items);
	useEffect(() => {
		if (lastItemsRef.current === items) return;
		lastItemsRef.current = items;
		setDropKey(null);
		setFormKey(null);
		if (returningRef.current) {
			returningRef.current = false;
			// Pane choices are kept: the entries they belong to may well still be here.
			setEntries((current) => reconcileQueue(current, items));
			return;
		}
		setEntries(buildQueue(items));
		setChoices({});
		setDrafts({});
	}, [items]);

	// Bind, then save the rules, then await the binding surfaces' refetch — the
	// mutations return it from `onSettled`, so `mutateAsync` resolves only once
	// the agent's rows read the new binding with its rules. Silent: every outcome
	// is reported on its own row.
	const bindMutation = useBindCredentialToAgents();
	const applyRules = useApplyBindingRules();
	// A brand-new binding's rules have no reader yet; read them in before the
	// item reads Added, so its row mounts on its real status.
	const primeRules = usePrimeBindingPermissions(agentId);
	const importMutation = useImportCatalogEntry();
	const { connect: runConnect, deviceDialog } = useDeviceAwareConnect();

	// A credential this queue has bound can't be bound to the agent again (a 409),
	// even when it also covers a later API — so it leaves every later pane.
	const active = useMemo(() => {
		const entry = activeEntry(entries);
		if (!entry) return null;
		const bound = new Set(
			entries.flatMap((e) =>
				e.status === 'added' && e.credentialId
					? [e.credentialId]
					: e.bound && e.chosen
						? [e.chosen.credential_id]
						: [],
			),
		);
		const covering = entry.covering.filter((c) => !bound.has(c.credential_id));
		return covering.length === entry.covering.length ? entry : { ...entry, covering };
	}, [entries]);
	const summary = useMemo(() => queueSummary(entries), [entries]);
	const blocked = useMemo(() => blockedCount(entries), [entries]);
	const formEntry = useMemo(
		() => entries.find((e) => e.key === formKey) ?? null,
		[entries, formKey],
	);

	// Keep the live row marked so the pane and the progress list never disagree
	// about which API is being worked on.
	useEffect(() => {
		setEntries((current) => markActive(current));
	}, [entries]);

	// Focus follows the work: a step change, or the next API, lands on its pane
	// (the sheet's own initial focus covers the first one).
	const activeStepKey = active ? `${active.key}:${active.step ?? 'credential'}` : null;
	const lastStepKey = useRef(activeStepKey);
	useEffect(() => {
		if (lastStepKey.current === activeStepKey) return;
		const hadOne = lastStepKey.current != null;
		lastStepKey.current = activeStepKey;
		if (hadOne && activeStepKey) paneRef.current?.focus({ preventScroll: true });
	}, [activeStepKey]);

	const draftFor = (key: string): AccessDraft => drafts[key] ?? DEFAULT_DRAFT;
	const setDraft = (key: string, patch: Partial<AccessDraft>): void =>
		setDrafts((current) => ({
			...current,
			[key]: { ...(current[key] ?? DEFAULT_DRAFT), ...patch },
		}));

	/** The credential step's answer: on to the access step, nothing bound yet. */
	const pick = (entry: QueueEntry, chosen: ChosenCredential): void =>
		setEntries((current) => chooseCredential(current, entry.key, chosen));

	/** Record the item as added — after its sign-in, when one is outstanding. An
	 * unfinished sign-in does NOT undo the bind: the row says it can't serve yet. */
	const finish = async (
		entry: QueueEntry,
		chosen: ChosenCredential,
		access: QueueAccess,
		ruleCount: number,
	): Promise<void> => {
		await primeRules(chosen.credential_id);
		let note: string | undefined;
		if (chosen.connect) {
			const outcome = await runConnect(chosen.credential_id, chosen.name).catch(
				() => ({ status: 'cancelled' }) as const,
			);
			if (outcome.status !== 'connected' && outcome.status !== 'redirected') {
				note = 'Sign-in not finished — the API is added but cannot be called yet.';
			}
		}
		setEntries((current) =>
			patchEntry(current, entry.key, {
				status: 'added',
				credentialId: chosen.credential_id,
				credentialName: chosen.name,
				access,
				ruleCount,
				bound: undefined,
				rulesError: undefined,
				note,
			}),
		);
	};

	/** Take one item to its terminal state: import the API if needed, bind the
	 * credential, save its rules (none for `later`), await the refetch, then run
	 * the consent flow if sign-in is outstanding. */
	const inFlight = useRef<Set<string>>(new Set());
	const commit = async (
		entry: QueueEntry,
		rules: PermissionRuleInput[] | null,
		access: QueueAccess,
	): Promise<void> => {
		const chosen = entry.chosen;
		if (!chosen || inFlight.current.has(entry.key)) return;
		inFlight.current.add(entry.key);
		setEntries((current) =>
			patchEntry(current, entry.key, {
				status: 'working',
				error: undefined,
				rulesError: undefined,
			}),
		);
		try {
			if (entry.bound) {
				// The binding landed on an earlier try; only its rules are owed.
				if (rules) {
					const { rulesFailed } = await applyRules.mutateAsync({
						credentialId: chosen.credential_id,
						agentIds: [agentId],
						rules,
					});
					if (rulesFailed.length > 0) {
						setEntries((current) =>
							patchEntry(current, entry.key, {
								status: 'active',
								rulesError: RULES_SAVE_FAILED,
							}),
						);
						return;
					}
				}
			} else {
				// The wizard already imported an unregistered catalog API on save.
				const imported = entry.created?.credential_id === chosen.credential_id;
				if (entry.importsApi && entry.api.apiId && !imported) {
					await importMutation.mutateAsync(entry.api.apiId);
				}
				let rulesFailed: string[];
				try {
					({ rulesFailed } = await bindMutation.mutateAsync({
						credentialId: chosen.credential_id,
						agentIds: [agentId],
						rules: rules ?? undefined,
					}));
				} catch (e) {
					setEntries((current) =>
						patchEntry(current, entry.key, { status: 'failed', error: errorText(e) }),
					);
					return;
				}
				if (rulesFailed.length > 0) {
					// Bound, blocked: Retry saves just the rules; the draft is kept.
					setEntries((current) =>
						patchEntry(current, entry.key, {
							status: 'active',
							bound: true,
							rulesError: RULES_SAVE_FAILED,
						}),
					);
					return;
				}
			}
			await finish(entry, chosen, access, rules?.length ?? 0);
		} catch (e) {
			setEntries((current) =>
				patchEntry(current, entry.key, { status: 'failed', error: errorText(e) }),
			);
		} finally {
			inFlight.current.delete(entry.key);
		}
	};

	/** Set up later: no rules to send. On an item whose binding already landed,
	 * `commit` skips straight to `finish` — still through the in-flight guard
	 * and the working status, so a second click can't start a second sign-in
	 * and Close waits for the one under way. */
	const later = (entry: QueueEntry): void => void commit(entry, null, 'later');

	const drop = (entry: QueueEntry): void => {
		setEntries((current) => patchEntry(current, entry.key, { status: 'dropped' }));
		setDropKey(null);
	};

	const handleCreated = (entry: QueueEntry, info: CreatedCredentialInfo): void => {
		setFormKey(null);
		const created = {
			credential_id: info.credentialId,
			name: info.name,
			needsConnect: info.needsConnect,
		};
		setEntries((current) =>
			chooseCredential(patchEntry(current, entry.key, { created }), entry.key, {
				credential_id: created.credential_id,
				name: created.name,
				connect: created.needsConnect,
			}),
		);
	};

	/** A vendor or shared-app sign-in from the form already bound the new
	 * credential to this agent (its rules set in that flow), and the server
	 * imported the catalog API at connect, so the row is simply added — no
	 * access step, which would bind it a second time. */
	const handleVendorConnected = (entry: QueueEntry, info: ConnectedCredentialInfo): void => {
		setEntries((current) =>
			patchEntry(current, entry.key, {
				status: 'added',
				error: undefined,
				credentialId: info.credentialId,
				credentialName: info.name,
			}),
		);
	};

	const retry = (key: string): void => setEntries((current) => retryEntry(current, key));
	// A bind in flight has to land before the queue can hand its item back —
	// reopening on it would bind the same credential again.
	const busy = entries.some((e) => e.status === 'working');
	const close = (): void => {
		// A binding whose rules never saved is attached, blocked — say so.
		const settled = settleBoundOnClose(entries);
		setEntries(settled);
		onClose(unfinishedItems(settled));
	};
	const goBack = (): void => {
		if (!onBack) return;
		setDiscardOpen(false);
		setFormKey(null);
		setDropKey(null);
		returningRef.current = true;
		setFocusPane(true);
		onBack(queueBackSeed(entries), unfinishedItems(entries));
	};
	/** Back from the credential form: a typed-in draft is confirmed away first; an
	 * untouched one is nothing to lose. */
	const backFromForm = (dirty: boolean): void => {
		if (dirty) setDiscardOpen(true);
		else goBack();
	};
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
			initialFocus={focusPane && active ? paneRef : undefined}
			className="sm:w-[560px] xl:w-[640px]"
		>
			<div className="flex h-full flex-col">
				<SheetHeader className="justify-between">
					<div className="min-w-0">
						{onBack && (
							<Button
								variant="ghost"
								size="sm"
								onClick={goBack}
								disabled={busy}
								className="mb-1 -ml-2 h-7 px-2 text-xs"
							>
								<ArrowLeft className="h-3.5 w-3.5" />
								Back to APIs
							</Button>
						)}
						<h2
							id={headingId}
							className="font-heading text-foreground-name text-base font-semibold"
						>
							Set up {summary.total} {summary.total === 1 ? 'API' : 'APIs'}
						</h2>
						<p className="text-muted-foreground text-xs">
							Each one gets a credential and access rules before{' '}
							<AgentNameText name={agentName} /> can call it.
						</p>
					</div>
					<Button
						variant="ghost"
						size="icon-xs"
						aria-label="Close"
						onClick={close}
						disabled={busy}
						className="shrink-0"
					>
						<X className="h-4 w-4" />
					</Button>
				</SheetHeader>

				<SheetBody className="space-y-4">
					{active ? (
						<ActivePane
							paneRef={paneRef}
							entry={active}
							agentName={agentName}
							position={{
								index: entries.findIndex((e) => e.key === active.key) + 1,
								total: entries.length,
							}}
							dropPending={dropKey === active.key}
							selected={choices[active.key] ?? defaultChoice(active)}
							onSelect={(next): void =>
								setChoices((current) => ({ ...current, [active.key]: next }))
							}
							onUseCredential={(credential): void =>
								pick(active, {
									credential_id: credential.credential_id,
									name: credential.name,
									// A credential whose first sign-in never completed
									// still needs that click before it can serve.
									connect: credentialAwaitsConsent(credential),
									reach: apiScopeReach(credential.api),
								})
							}
							onUseCreated={(created): void =>
								pick(active, {
									credential_id: created.credential_id,
									name: created.name,
									connect: created.needsConnect,
								})
							}
							onOpenForm={(): void => setFormKey(active.key)}
							onAskDrop={(): void => setDropKey(active.key)}
							onCancelDrop={(): void => setDropKey(null)}
							onConfirmDrop={(): void => drop(active)}
							access={
								active.step === 'access' && active.chosen ? (
									<AccessStep
										key={active.key}
										entry={active}
										chosen={active.chosen}
										agentName={agentName}
										draft={draftFor(active.key)}
										onDraftChange={(patch): void => setDraft(active.key, patch)}
										onBack={(): void =>
											setEntries((current) =>
												backToCredential(current, active.key),
											)
										}
										onCommit={(rules, preset): void =>
											void commit(active, rules, preset)
										}
										onLater={(): void => later(active)}
									/>
								) : null
							}
						/>
					) : (
						<DonePane summary={summary} blocked={blocked} />
					)}

					<ProgressList
						entries={entries}
						activeKey={active?.key ?? null}
						onRetry={retry}
					/>
				</SheetBody>

				<SheetFooter className="block p-0">
					<div className="bg-surface-tonal h-0.5 w-full" aria-hidden="true">
						<div
							className="bg-primary h-full transition-[width] duration-300 ease-out motion-reduce:transition-none"
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
						<p className="text-muted-foreground text-xs leading-snug">
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
				</SheetFooter>
			</div>

			{formEntry && (
				<CreateCredentialFlow
					key={formEntry.key}
					open
					pinnedApi={formEntry.api}
					preselectedAgentId={agentId}
					onVendorConnected={(info): void => handleVendorConnected(formEntry, info)}
					onClose={(): void => setFormKey(null)}
					onCreated={(info): void => handleCreated(formEntry, info)}
					back={onBack ? { label: 'Back to APIs', onBack: backFromForm } : undefined}
				/>
			)}
			<ConfirmDialog
				open={discardOpen}
				title="Discard this credential?"
				body={
					<>
						What you typed for {formEntry?.api.label ?? 'this API'} won&apos;t be saved.
						APIs already added stay added.
					</>
				}
				confirmLabel="Discard and go back"
				onConfirm={goBack}
				onClose={(): void => setDiscardOpen(false)}
			/>
			{deviceDialog}
		</SheetPrimitive>
	);
}

/** The pane for the item at the front of the queue.
 *
 * An API that existing credentials cover shows them all as cards, with "Add a new
 * credential" last and a lone covering credential preselected — never bound
 * until confirmed: reuse matches API identity, not account, so a wrong-tenant
 * match must be rejectable without dropping the API. The primary action follows
 * the selected card. */
function ActivePane({
	paneRef,
	entry,
	agentName,
	position,
	dropPending,
	selected,
	onSelect,
	onUseCredential,
	onUseCreated,
	onOpenForm,
	onAskDrop,
	onCancelDrop,
	onConfirmDrop,
	access,
}: {
	paneRef: React.RefObject<HTMLElement | null>;
	entry: QueueEntry;
	agentName: string;
	/** "API 2 of 3" — where this item sits in the batch. */
	position: { index: number; total: number };
	dropPending: boolean;
	selected: CredentialChoice | null;
	onSelect: (choice: CredentialChoice) => void;
	onUseCredential: (credential: Credential) => void;
	/** Carry on with the credential this queue created for the item. */
	onUseCreated: (created: NonNullable<QueueEntry['created']>) => void;
	onOpenForm: () => void;
	onAskDrop: () => void;
	onCancelDrop: () => void;
	onConfirmDrop: () => void;
	/** The access step, when the item is on it. */
	access: React.ReactNode;
}) {
	const working = entry.status === 'working';
	const onAccess = access != null;
	const created = entry.created ?? null;
	const count = entry.covering.length;
	const existing =
		selected?.kind === 'existing'
			? (entry.covering.find((c) => c.credential_id === selected.credentialId) ?? null)
			: null;
	const wantsNew = count === 0 || selected?.kind === 'new';
	/** A new credential for this API is one sign-in click — the tray established it. */
	const newIsSignIn = entry.outcome === 'oauth';
	/** Its spec declares no authentication, so the new credential carries no secret. */
	const newIsNoAuth = entry.outcome === 'no-auth';
	const other = entry.existing.length > 0 ? ' other' : '';

	return (
		<section
			ref={paneRef}
			// Programmatic focus only — the landing spot after a Back round trip.
			tabIndex={-1}
			aria-label={`Set up ${entry.api.label}`}
			data-testid="queue-active-pane"
			className="bg-surface-inset focus-visible:ring-ring space-y-3 rounded-lg p-4 outline-none focus-visible:ring-2"
		>
			<div className="flex items-start gap-3">
				<span
					aria-hidden
					className="bg-surface-field flex h-9 w-9 shrink-0 items-center justify-center rounded-md"
				>
					<KeyRound className="text-muted-foreground h-4 w-4" />
				</span>
				<div className="min-w-0 flex-1">
					<p className="text-foreground-name truncate text-sm font-semibold">
						{entry.api.label}
					</p>
					<p
						data-testid="queue-active-identity"
						className="text-muted-foreground font-mono text-xs"
					>
						{/* A catalog pick's name is its whole `api_id`, which already
						    leads with the vendor (or is just the vendor). */}
						{entry.api.name === entry.api.vendor
							? entry.api.name
							: apiIdentityTuple({ vendor: entry.api.vendor, name: entry.api.name })}
					</p>
				</div>
				{working ? (
					<span className="text-muted-foreground inline-flex shrink-0 items-center gap-1.5 text-xs">
						<Loader2 className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />
						Adding…
					</span>
				) : (
					position.total > 1 && (
						<span
							data-testid="queue-position"
							className="text-muted-foreground shrink-0 text-xs tabular-nums"
						>
							{position.index} of {position.total}
						</span>
					)
				)}
			</div>

			<StepIndicator step={onAccess ? 'access' : 'credential'} />

			{onAccess ? (
				access
			) : (
				<>
					{entry.existing.length > 0 && (
						<div className="space-y-1.5">
							<p
								data-testid="queue-existing-accounts"
								className="text-muted-foreground text-xs"
							>
								{addedViaLabel(entry.existing)}. Pick another credential to add it
								to <AgentNameText name={agentName} />.
							</p>
							<p
								data-testid="queue-ambiguity-warning"
								className="text-foreground flex items-start gap-2 text-xs"
							>
								<AlertTriangle className="text-warning mt-0.5 h-3.5 w-3.5 shrink-0" />
								<span>{anotherCredentialWarning(entry.api.label)}</span>
							</p>
						</div>
					)}

					{created ? (
						<p
							data-testid="queue-created-credential"
							className="text-muted-foreground text-xs"
						>
							Uses the credential you created for {entry.api.label},{' '}
							<span className="text-foreground font-medium">{created.name}</span>.
						</p>
					) : (
						count > 0 && (
							<CredentialOptions
								id={`queue-credential-${entry.key}`}
								legend={
									count === 1
										? `You have 1${other} credential for ${entry.api.label}. Should ${agentName} use it, or a new one?`
										: `You have ${count}${other} credentials for ${entry.api.label}. Which should ${agentName} use?`
								}
								credentials={entry.covering}
								selected={selected}
								onSelect={onSelect}
								newCredentialDetail="Another key or account for this API — nothing is stored until you save it"
								disabled={working || dropPending}
							/>
						)
					)}

					{existing && credentialAwaitsConsent(existing) && (
						<p className="text-muted-foreground text-xs">
							<span className="text-foreground font-medium">{existing.name}</span> is
							ready to use — it just needs you to finish signing in, once its access
							is set.
						</p>
					)}

					{count === 0 && !created && (
						<p className="text-muted-foreground text-xs">
							{newIsSignIn
								? 'Sign in once and this API is set up — there is nothing to type in.'
								: newIsNoAuth
									? "This API's spec declares no authentication, so its credential has no secret to enter."
									: 'This API needs a new credential. Nothing is stored until you save it.'}
						</p>
					)}

					{dropPending ? (
						<div
							className="bg-surface-inset space-y-2 rounded-md p-3"
							data-testid="queue-drop-confirm"
						>
							<p className="text-foreground flex items-start gap-2 text-xs">
								<AlertTriangle className="text-caution mt-0.5 h-3.5 w-3.5 shrink-0" />
								{/* There is no "later" state to drop into. */}
								<span>
									{dropWarning(entry.api.label)}{' '}
									<AgentNameText name={agentName} /> will not be able to call it,
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
							{created ? (
								<Button
									size="sm"
									disabled={working}
									onClick={(): void => onUseCreated(created)}
								>
									<Check className="h-4 w-4" />
									Use this credential
								</Button>
							) : existing ? (
								<Button
									size="sm"
									disabled={working}
									onClick={(): void => onUseCredential(existing)}
								>
									<Check className="h-4 w-4" />
									Use this credential
								</Button>
							) : wantsNew ? (
								<Button size="sm" disabled={working} onClick={onOpenForm}>
									{newIsNoAuth ? (
										<LockOpen className="h-4 w-4" />
									) : (
										<KeyRound className="h-4 w-4" />
									)}
									{newIsNoAuth ? 'Add without a secret' : 'Add credential'}
								</Button>
							) : (
								// Nothing selected yet, which only happens among several: the
								// button waits for a card rather than guessing one.
								<Button size="sm" disabled>
									<Check className="h-4 w-4" />
									Use this credential
								</Button>
							)}
							<Button
								size="sm"
								variant="ghost"
								disabled={working}
								onClick={onAskDrop}
							>
								Not this one
							</Button>
						</div>
					)}
				</>
			)}
		</section>
	);
}

/** Credential → Access: where this item's setup stands. */
function StepIndicator({ step }: { step: 'credential' | 'access' }) {
	const steps = [
		{ key: 'credential', label: 'Credential' },
		{ key: 'access', label: 'Access' },
	] as const;
	const at = steps.findIndex((s) => s.key === step);
	return (
		<ol
			aria-label="Setup steps"
			className="flex items-center gap-2 text-xs"
			data-testid="queue-steps"
		>
			{steps.map((s, i) => (
				<li
					key={s.key}
					aria-current={i === at ? 'step' : undefined}
					className={cn(
						'inline-flex items-center gap-1.5',
						i === at ? 'text-foreground font-medium' : 'text-muted-foreground',
					)}
				>
					{i > 0 && (
						<span aria-hidden className="text-muted-foreground">
							→
						</span>
					)}
					<span
						aria-hidden
						className={cn(
							'flex h-4 w-4 items-center justify-center rounded-full text-[10px] tabular-nums',
							i < at
								? 'bg-success/15 text-success'
								: i === at
									? 'bg-primary text-primary-foreground'
									: 'bg-surface-field',
						)}
					>
						{i < at ? <Check className="h-2.5 w-2.5" /> : i + 1}
					</span>
					{s.label}
					{i < at && <span className="sr-only"> (done)</span>}
				</li>
			))}
		</ol>
	);
}

/** The access step: what the agent may call through the chosen credential — the
 * workspace bind's presets, with an inline dry run of the rules as edited. */
function AccessStep({
	entry,
	chosen,
	agentName,
	draft,
	onDraftChange,
	onBack,
	onCommit,
	onLater,
}: {
	entry: QueueEntry;
	chosen: ChosenCredential;
	agentName: string;
	draft: AccessDraft;
	onDraftChange: (patch: Partial<AccessDraft>) => void;
	onBack: () => void;
	onCommit: (rules: PermissionRuleInput[], preset: RulesPreset) => void;
	onLater: () => void;
}) {
	const working = entry.status === 'working';
	const [laterPending, setLaterPending] = useState(false);
	// Focus follows the confirm in and back out, so the keyboard never drops
	// to the page when the button that opened it unmounts.
	const laterRef = useRef<HTMLButtonElement>(null);
	const keepRef = useRef<HTMLButtonElement>(null);
	const laterOpened = useRef(false);
	useEffect(() => {
		if (laterPending) keepRef.current?.focus();
		else if (laterOpened.current) laterRef.current?.focus();
		laterOpened.current = laterPending;
	}, [laterPending]);
	// A credential this queue created carries no scope in hand — read it.
	const fetched = useCredential(chosen.reach ? undefined : chosen.credential_id);
	const reach: ScopeReach =
		chosen.reach ?? (fetched.data ? apiScopeReach(fetched.data.api) : 'pinned');
	// Only a workspace API has operations to read before it is imported.
	const apiReference =
		entry.api.source === 'local' && entry.api.version
			? { vendor: entry.api.vendor, name: entry.api.name, version: entry.api.version }
			: null;
	const rules = useMemo(
		() => rulesForPreset(draft.preset, draft.customRules),
		[draft.preset, draft.customRules],
	);

	return (
		<div className="space-y-3" data-testid="queue-access-step">
			<p className="text-muted-foreground text-xs">
				Through <span className="text-foreground font-medium">{chosen.name}</span>. A new
				binding denies every call until it has rules — say what{' '}
				<AgentNameText name={agentName} /> may call.
			</p>

			<AccessRulesStep
				reach={reach}
				preset={draft.preset}
				onPresetChange={(preset): void => onDraftChange({ preset })}
				customRules={draft.customRules}
				onCustomRulesChange={(customRules): void => onDraftChange({ customRules })}
				apiReference={apiReference}
				disabled={working}
				heading={
					<>
						What can <AgentNameText name={agentName} /> call?
					</>
				}
				tester
				testIdPrefix="queue-access"
			/>

			{entry.rulesError && (
				<div data-testid="queue-rules-failed">
					<ErrorAlert
						title={entry.rulesError}
						message={`${entry.api.label} is added but blocked — every call is denied until its rules are saved. Your rules are kept.`}
						onRetry={
							rules
								? (): void => onCommit(rules, draft.preset as RulesPreset)
								: undefined
						}
						retrying={working}
					/>
				</div>
			)}

			{laterPending ? (
				<div
					className="bg-surface-sheet space-y-2 rounded-md p-3"
					data-testid="queue-later-confirm"
					role="group"
					aria-label="Set up later"
				>
					<p className="text-foreground flex items-start gap-2 text-xs">
						<AlertTriangle className="text-caution mt-0.5 h-3.5 w-3.5 shrink-0" />
						<span>
							<AgentNameText name={agentName} /> won&apos;t be able to call{' '}
							{entry.api.label} until you add rules — every call is denied, and it
							shows as Blocked until then.
						</span>
					</p>
					<div className="flex items-center gap-2">
						<Button size="sm" variant="secondary" loading={working} onClick={onLater}>
							Add without rules
						</Button>
						<Button
							ref={keepRef}
							size="sm"
							variant="ghost"
							disabled={working}
							onClick={(): void => setLaterPending(false)}
						>
							Keep setting up
						</Button>
					</div>
				</div>
			) : (
				<div className="flex flex-wrap items-center gap-2">
					<Button
						size="sm"
						disabled={working || rules == null}
						loading={working}
						onClick={(): void => {
							if (rules && draft.preset) onCommit(rules, draft.preset);
						}}
					>
						<ShieldCheck className="h-4 w-4" />
						{entry.bound
							? 'Save rules'
							: chosen.connect
								? `Add ${entry.api.label} and sign in`
								: `Add ${entry.api.label}`}
					</Button>
					<Button
						ref={laterRef}
						size="sm"
						variant="ghost"
						disabled={working}
						onClick={(): void => setLaterPending(true)}
					>
						Set up later
					</Button>
					{!entry.bound && (
						<Button
							size="sm"
							variant="ghost"
							disabled={working}
							onClick={onBack}
							className="ml-auto"
						>
							<ArrowLeft className="h-3.5 w-3.5" />
							Credential
						</Button>
					)}
				</div>
			)}
			{rules == null && draft.preset === 'custom' && !laterPending && (
				<p className="text-muted-foreground text-xs" data-testid="queue-access-hint">
					Add at least one rule, or pick a preset.
				</p>
			)}
		</div>
	);
}

/** The outcome of the batch. Everything dropped or failed is not a success — no
 * success mark, no "now set the rules" follow-up. */
function DonePane({ summary, blocked }: { summary: QueueSummary; blocked: number }) {
	const nothingAdded = summary.added === 0;
	return (
		<section
			aria-label="Setup finished"
			data-testid="queue-done-pane"
			className="bg-surface-inset flex items-start gap-3 rounded-lg p-4"
		>
			<span
				aria-hidden
				className={cn(
					'flex h-9 w-9 shrink-0 items-center justify-center rounded-lg',
					nothingAdded ? 'bg-surface-field' : 'bg-success/10',
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
						: blocked > 0
							? `${blocked === 1 ? '1 API was' : `${blocked} APIs were`} set up later — every call to ${blocked === 1 ? 'it' : 'them'} is denied until you add rules on the API.`
							: 'Each API was added with its access rules.'}
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

/** What an added row's binding was given — custom rules counted. */
function accessLabel(entry: QueueEntry): string {
	if (entry.access === 'custom') {
		const n = entry.ruleCount ?? 0;
		return `${n} custom ${n === 1 ? 'rule' : 'rules'}`;
	}
	return entry.access ? QUEUE_ACCESS_LABELS[entry.access] : '';
}

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
						entry.key === activeKey && 'bg-tint-2',
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
					{/* Which credential it went through — the batch's record once the
					    pane has moved on. */}
					{entry.status === 'added' && entry.credentialName && (
						<span className="text-muted-foreground shrink-0 text-xs">
							via {entry.credentialName}
						</span>
					)}
					{entry.status === 'added' && entry.access && (
						<span
							data-testid="queue-row-access"
							className={cn(
								'shrink-0 text-xs',
								entry.access === 'later' ? 'text-caution' : 'text-muted-foreground',
							)}
						>
							· {accessLabel(entry)}
						</span>
					)}
					{entry.status === 'failed' && (
						<>
							<Badge variant="danger" className="shrink-0">
								{entry.error}
							</Badge>
							<Button
								size="xs"
								variant="tonal"
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
