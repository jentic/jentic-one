/**
 * ApiSetupQueue — step 2 of the Add-APIs flow: finish the batch the tray handed
 * over, one API at a time from the list: ① its credential, then ② its access.
 *
 * Nothing is bound until the operator adds the API; that binds the credential,
 * then saves its rules, and only moves on once the binding surfaces refetched, so
 * rows never read a stale "Blocked". A failed rules save keeps the binding and
 * offers Retry. Back keeps this mounted; {@link reconcileQueue} folds the edited
 * batch back in so per-item progress survives.
 *
 * An API the agent already reaches is set up the same way: the pane names the
 * credentials it has and offers only unbound ones (rules are per binding).
 */
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useReducedMotionConfig } from 'framer-motion';
import {
	AlertTriangle,
	ArrowLeft,
	Check,
	ChevronDown,
	ChevronRight,
	KeyRound,
	Minus,
	ShieldCheck,
	ShieldOff,
	X,
} from 'lucide-react';
import {
	Button,
	ConfirmDialog,
	DetailsLine,
	ErrorAlert,
	ExpandReveal,
	REVEAL_MOTION,
	SheetFooter,
	SheetHeader,
	SheetPrimitive,
	Tooltip,
	VendorIcon,
	type PermissionRuleInput,
} from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import { useCredential, useImportCatalogEntry } from '@/shared/credentials/api';
import {
	useApplyBindingRules,
	useBindCredentialToAgents,
} from '@/shared/credentials/api/vendors-hooks';
import type { PermissionRule } from '@/shared/credentials/api/vendors-types';
import {
	AccessRulesStep,
	useDeviceAwareConnect,
	type ConnectedCredentialInfo,
	CreateCredentialFlow,
	type CreatedCredentialInfo,
	CredentialOptions,
	useUnappliedChoices,
	type UnappliedChoices,
} from '@/shared/credentials/components';
import {
	presetForRules,
	rulesForPreset,
	type RulesPreset,
	type ScopeReach,
} from '@/shared/credentials/lib/accessPresets';
import { apiScopeReach } from '@/shared/credentials/lib/apiIdentity';
import {
	credentialAwaitsConsent,
	type CredentialChoice,
} from '@/shared/credentials/lib/credentialIdentity';
import {
	addedViaLabel,
	anotherCredentialWarning,
	defaultChoice,
	type PreflightItem,
} from '@/modules/agents/lib/apiPreflight';
import {
	AGENT_SETUP_FLOW,
	QUEUE_ACCESS_LABELS,
	QUEUE_STATUS_LABELS,
	activeEntry,
	blockedCount,
	buildQueue,
	chooseCredential,
	isTerminal,
	patchEntry,
	queueBackSeed,
	queueSummary,
	queueSummaryLine,
	reconcileQueue,
	reopenEntry,
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
import { useQueueApiReference } from '@/modules/agents/lib/useQueueApiReference';

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

/** One item's access draft: the preset and its custom rules. */
interface AccessDraft {
	preset: RulesPreset | null;
	customRules: PermissionRule[];
}

/** Read only by default: the least access that still lets it work. */
const DEFAULT_DRAFT: AccessDraft = { preset: 'read', customRules: [] };

const RULES_SAVE_FAILED = "The access rules weren't saved.";

/** The sr-only reason the footer's primary references via `aria-describedby`. */
const ADD_REASON_ID = 'queue-add-reason';

/** Used only once reach is known: as a stand-in it would under-state Full access. */
const DEFAULT_SCOPE_REACH: ScopeReach = 'pinned';

/** The credential an existing-credential choice stands for, ready to bind. */
function chosenFromExisting(
	entry: QueueEntry,
	choice: CredentialChoice | null,
	/** Reach of the credentials this queue created (absent from the preflight). */
	createdReach: Record<string, ScopeReach>,
): ChosenCredential | null {
	if (choice?.kind !== 'existing') return null;
	const created = entry.created;
	if (created?.credential_id === choice.credentialId) {
		return {
			credential_id: created.credential_id,
			name: created.name,
			connect: created.needsConnect,
			reach: createdReach[created.credential_id],
		};
	}
	const credential = entry.covering.find((c) => c.credential_id === choice.credentialId);
	if (!credential) return null;
	return {
		credential_id: credential.credential_id,
		name: credential.name,
		// A first sign-in that never completed still needs its click.
		connect: credentialAwaitsConsent(credential),
		reach: apiScopeReach(credential.api),
	};
}

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
	/** The API the operator picked in the list; absent = the first still owed. */
	const [openKey, setOpenKey] = useState<string | null>(null);
	/** The entry whose inline credential form is unfolded, and whether it was typed in. */
	const [creatingKey, setCreatingKey] = useState<string | null>(null);
	const [creatingDirty, setCreatingDirty] = useState(false);
	/** Entry key → the picked credential radio. Absent = {@link defaultChoice}. */
	const [choices, setChoices] = useState<Record<string, CredentialChoice>>({});
	/** Entry key → its access draft. Absent = {@link DEFAULT_DRAFT}. Kept across
	 * Back (to the tray) until the queue is replaced. */
	const [drafts, setDrafts] = useState<Record<string, AccessDraft>>({});
	/** Credential id → scope reach from the create response (not in the preflight). */
	const [createdReach, setCreatedReach] = useState<Record<string, ScopeReach>>({});
	/** Per item: the catalog import its new credential queued, for ②'s operations. */
	const [importJobs, setImportJobs] = useState<Record<string, string>>({});
	/** A typed-in credential is confirmed away before anything discards it. */
	const [discard, setDiscard] = useState<{ then: () => void } | null>(null);
	/** Set by Back: the next batch is the same one, edited in the tray, so it is
	 * folded into the progress rather than replacing it. */
	const returningRef = useRef(false);
	/** After a round trip, the pane — not the header's first button — takes focus. */
	const [focusPane, setFocusPane] = useState(false);
	const paneRef = useRef<HTMLDivElement>(null);
	/** ② Access, which a saved credential scrolls into view and focuses. */
	const accessRef = useRef<HTMLDivElement>(null);
	const reduceMotion = useReducedMotionConfig() ?? false;
	/** `revealAccess`'s pending scroll, cleared on unmount. */
	const revealTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
	useEffect(
		() => () => {
			if (revealTimer.current) clearTimeout(revealTimer.current);
		},
		[],
	);
	const revealAccess = (): void => {
		if (revealTimer.current) clearTimeout(revealTimer.current);
		// After ① folds and ② opens, so the scroll lands where ② settles.
		revealTimer.current = setTimeout(
			() => {
				revealTimer.current = null;
				const el = accessRef.current;
				if (!el) return;
				el.scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'start' });
				el.querySelector<HTMLElement>('[role="radio"][tabindex="0"]')?.focus({
					preventScroll: true,
				});
			},
			reduceMotion ? 0 : REVEAL_MOTION.openMs + 60,
		);
	};

	// A new batch replaces the queue outright. Compared by reference: a content
	// compare would fight the in-progress statuses.
	const lastItemsRef = useRef(items);
	useEffect(() => {
		if (lastItemsRef.current === items) return;
		lastItemsRef.current = items;
		setCreatingKey(null);
		setCreatingDirty(false);
		if (returningRef.current) {
			returningRef.current = false;
			// Pane choices are kept: the entries they belong to may well still be here.
			setEntries((current) => reconcileQueue(current, items));
			return;
		}
		setEntries(buildQueue(items));
		setOpenKey(null);
		setChoices({});
		setDrafts({});
		setCreatedReach({});
		setImportJobs({});
		setChangingKey(null);
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

	// The API on screen: the one picked, else the first still owed. A credential bound
	// here leaves every other pane (binding it again would 409).
	const current = useMemo(() => {
		const entry = entries.find((e) => e.key === openKey) ?? activeEntry(entries);
		if (!entry) return null;
		const bound = new Set(
			entries.flatMap((e) =>
				e.key === entry.key
					? []
					: e.status === 'added' && e.credentialId
						? [e.credentialId]
						: e.bound && e.chosen
							? [e.chosen.credential_id]
							: [],
			),
		);
		const covering = entry.covering.filter((c) => !bound.has(c.credential_id));
		return covering.length === entry.covering.length ? entry : { ...entry, covering };
	}, [entries, openKey]);
	const summary = useMemo(() => queueSummary(entries), [entries]);
	const blocked = useMemo(() => blockedCount(entries), [entries]);
	const nextOwed = (after: string | null): QueueEntry | undefined =>
		entries.find((e) => e.key !== after && !isTerminal(e.status));

	// An API with nothing to choose from opens on the new-credential form.
	const currentKey = current?.key ?? null;

	// Bring the on-screen API's row into view only when it changes, so browsing the
	// list by scroll is never pulled back.
	const railListRef = useRef<HTMLOListElement>(null);
	useEffect(() => {
		const list = railListRef.current;
		const row = list?.querySelector<HTMLElement>('[data-selected="true"]');
		if (!list || !row || list.scrollHeight <= list.clientHeight) return;
		const top =
			row.getBoundingClientRect().top - list.getBoundingClientRect().top + list.scrollTop;
		const bottom = top + row.offsetHeight;
		const margin = 8;
		let next: number | null = null;
		if (top < list.scrollTop + margin) next = top - margin;
		else if (bottom > list.scrollTop + list.clientHeight - margin)
			next = bottom - list.clientHeight + margin;
		if (next == null) return;
		list.scrollTo({ top: Math.max(0, next), behavior: reduceMotion ? 'auto' : 'smooth' });
	}, [currentKey, reduceMotion]);
	const needsForm =
		current != null &&
		!isTerminal(current.status) &&
		current.covering.length === 0 &&
		!current.created &&
		!current.chosen;
	useEffect(() => {
		if (needsForm && currentKey) {
			setCreatingKey(currentKey);
			setCreatingDirty(false);
		}
	}, [needsForm, currentKey]);

	// Focus follows the work onto the next API's pane.
	const lastKey = useRef(currentKey);
	useEffect(() => {
		if (lastKey.current === currentKey) return;
		const hadOne = lastKey.current != null;
		lastKey.current = currentKey;
		if (hadOne) paneRef.current?.focus({ preventScroll: true });
	}, [currentKey]);

	const draftFor = (key: string): AccessDraft => drafts[key] ?? DEFAULT_DRAFT;
	const setDraft = (key: string, patch: Partial<AccessDraft>): void =>
		setDrafts((current) => ({
			...current,
			[key]: { ...(current[key] ?? DEFAULT_DRAFT), ...patch },
		}));

	const choiceFor = (entry: QueueEntry): CredentialChoice | null =>
		choices[entry.key] ?? defaultChoice(entry);
	/** The credential Access works with: the confirmed one, else the selected radio. */
	const chosenFor = (entry: QueueEntry): ChosenCredential | null =>
		(entry.step === 'access' && entry.chosen) ||
		(creatingKey === entry.key
			? null
			: chosenFromExisting(entry, choiceFor(entry), createdReach));

	// ② By action's staged choices: settled (applied or discarded) before
	// anything moves on, never dropped silently.
	const unapplied = useUnappliedChoices();
	/** Leave a typed-in draft only once it is confirmed away. */
	const guardDraft = (then: () => void): void => {
		if (creatingDirty) setDiscard({ then });
		else unapplied.settle('before you leave this API', () => then());
	};
	const closeForm = (): void => {
		setCreatingKey(null);
		setCreatingDirty(false);
	};
	/** The entry whose ① chooser was reopened by "Change" (its choice still stands). */
	const [changingKey, setChangingKey] = useState<string | null>(null);
	/** Pick an API from the list — a skipped or failed one comes back to the work. */
	const openApi = (key: string): void => {
		if (key === currentKey) return;
		guardDraft(() => {
			closeForm();
			setChangingKey(null);
			setEntries((current) => reopenEntry(current, key));
			setOpenKey(key);
		});
	};

	/** An existing credential is chosen at once; "Add a new credential" unfolds the form. */
	const choose = (entry: QueueEntry, choice: CredentialChoice): void => {
		setChoices((current) => ({ ...current, [entry.key]: choice }));
		if (choice.kind === 'new') {
			// Reopening a folded form keeps its draft — and its dirty flag.
			if (creatingKey !== entry.key) setCreatingDirty(false);
			setCreatingKey(entry.key);
			return;
		}
		// The form folds; its typed values are kept (and still guarded).
		setCreatingKey(null);
		setChangingKey(null);
		const chosen = chosenFromExisting(entry, choice, createdReach);
		if (chosen) setEntries((current) => chooseCredential(current, entry.key, chosen));
	};
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
		// On to the next API still owed (or Done).
		setOpenKey((open) => (open === entry.key ? null : open));
	};

	/** Take one item to its terminal state: import the API if needed, bind the
	 * credential, save its rules (none for `later`), await the refetch, then run
	 * the consent flow if sign-in is outstanding. */
	const inFlight = useRef<Set<string>>(new Set());
	const commit = async (
		entry: QueueEntry,
		chosen: ChosenCredential | null,
		rules: PermissionRuleInput[] | null,
		access: QueueAccess,
	): Promise<void> => {
		if (!chosen || inFlight.current.has(entry.key)) return;
		inFlight.current.add(entry.key);
		setEntries((current) =>
			patchEntry(current, entry.key, {
				status: 'working',
				step: 'access',
				chosen,
				error: undefined,
				rulesError: undefined,
			}),
		);
		try {
			if (entry.bound) {
				// The binding landed on an earlier try; only its rules are owed. A throw keeps it
				// bound-and-blocked, not `failed`, whose "Try again" would re-bind (409).
				if (rules) {
					let rulesFailed: string[];
					try {
						({ rulesFailed } = await applyRules.mutateAsync({
							credentialId: chosen.credential_id,
							agentIds: [agentId],
							rules,
						}));
					} catch (e) {
						setEntries((current) =>
							patchEntry(current, entry.key, {
								status: 'active',
								rulesError: errorText(e),
							}),
						);
						return;
					}
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
	const later = (entry: QueueEntry): void => void commit(entry, chosenFor(entry), null, 'later');

	const drop = (entry: QueueEntry): void => {
		setEntries((current) => patchEntry(current, entry.key, { status: 'dropped' }));
		setOpenKey(nextOwed(entry.key)?.key ?? null);
	};

	const handleCreated = (entry: QueueEntry, info: CreatedCredentialInfo): void => {
		closeForm();
		setChangingKey(null);
		setChoices((current) => ({
			...current,
			[entry.key]: { kind: 'existing', credentialId: info.credentialId },
		}));
		// Recorded so ② never captions Full access with a reach it has yet to read.
		setCreatedReach((current) => ({ ...current, [info.credentialId]: info.reach }));
		const { importJobId } = info;
		if (importJobId) setImportJobs((current) => ({ ...current, [entry.key]: importJobId }));
		revealAccess();
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
				reach: info.reach,
			}),
		);
	};

	/** A vendor or shared-app sign-in from the form already bound the new
	 * credential to this agent (its rules set in that flow), and the server
	 * imported the catalog API at connect, so the row is simply added — no
	 * access step, which would bind it a second time. */
	const handleVendorConnected = (entry: QueueEntry, info: ConnectedCredentialInfo): void => {
		closeForm();
		setEntries((current) =>
			patchEntry(current, entry.key, {
				status: 'added',
				error: undefined,
				credentialId: info.credentialId,
				credentialName: info.name,
			}),
		);
	};

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
		guardDraft(() => {
			closeForm();
			returningRef.current = true;
			setFocusPane(true);
			onBack(queueBackSeed(entries), unfinishedItems(entries));
		});
	};
	// The form's own dialogs sit above the sheet; a typed draft is confirmed first.
	const guardedClose = (): void => {
		if (busy || discard || unapplied.settling) return;
		guardDraft(close);
	};

	const added = summary.added;
	const total = summary.total;
	const settledCurrent =
		current != null && isTerminal(current.status) && current.status !== 'failed';
	const creating = current != null && creatingKey === current.key;
	const chosen = current ? chosenFor(current) : null;
	const draft = current ? draftFor(current.key) : DEFAULT_DRAFT;
	const rules = rulesForPreset(draft.preset, draft.customRules);
	// A chosen credential's reach is read before Access is usable, never assumed `pinned`.
	const reachFetch = useCredential(chosen && !chosen.reach ? chosen.credential_id : undefined);
	const reachUnknown = chosen != null && !chosen.reach && !reachFetch.data;
	const reach: ScopeReach =
		chosen?.reach ??
		(reachFetch.data ? apiScopeReach(reachFetch.data.api) : DEFAULT_SCOPE_REACH);

	let primary: ReactNode;
	if (!current) {
		primary = (
			<Button onClick={close} data-testid="queue-done">
				Done
			</Button>
		);
	} else if (current.status === 'failed') {
		primary = (
			<Button onClick={(): void => setEntries((c) => reopenEntry(c, current.key))}>
				Try again
			</Button>
		);
	} else if (settledCurrent) {
		const next = nextOwed(current.key);
		primary = next ? (
			<Button onClick={(): void => openApi(next.key)}>
				Next: {next.api.label}
				<ChevronRight className="h-4 w-4" />
			</Button>
		) : (
			<Button onClick={close} data-testid="queue-done">
				Done
			</Button>
		);
	} else {
		const working = current.status === 'working';
		// Why "Add" can't run; `reachUnknown` blocks committing before the reach is read.
		const reason = !chosen
			? creating
				? 'Save the credential first'
				: 'Choose a credential first'
			: reachUnknown
				? 'Reading what this credential covers — the access wording depends on it'
				: rules == null && unapplied.staged === 0
					? 'Add at least one rule'
					: null;
		const blocked = reason != null;
		// `aria-disabled`, not `disabled`: it stays in the tab order, described by the
		// reason, and the click no-ops.
		const button = (
			<Button
				aria-disabled={blocked || working || undefined}
				aria-describedby={reason ? ADD_REASON_ID : undefined}
				loading={working}
				className={cn(
					blocked &&
						'bg-surface-quiet-cta text-foreground-sub hover:bg-surface-quiet-cta cursor-not-allowed active:scale-100',
				)}
				onClick={(): void => {
					if (blocked || working) return;
					unapplied.settle(`before adding ${current.api.label}`, (applied) => {
						// Applying staged choices changes the rules this add saves.
						const toSave = applied ? rulesForPreset(draft.preset, applied) : rules;
						if (chosen && toSave && draft.preset)
							// Custom rules that are exactly a preset's are recorded as that preset.
							void commit(
								current,
								chosen,
								toSave,
								presetForRules(toSave) ?? draft.preset,
							);
					});
				}}
				data-testid="queue-add"
			>
				<ShieldCheck className="h-4 w-4 shrink-0" />
				{current.bound ? (
					'Save rules'
				) : (
					<>
						<span className="sm:hidden">
							{chosen?.connect ? 'Add and sign in' : 'Add'}
						</span>
						<span className="hidden max-w-[22rem] truncate sm:inline">
							{chosen?.connect
								? `Add ${current.api.label} and sign in`
								: `Add ${current.api.label}`}
						</span>
					</>
				)}
			</Button>
		);
		primary = reason ? (
			<>
				<span id={ADD_REASON_ID} className="sr-only">
					{reason}
				</span>
				<Tooltip content={reason} interactiveChild>
					{button}
				</Tooltip>
			</>
		) : (
			button
		);
	}
	const canSkip = current != null && !isTerminal(current.status) && !current.bound && !busy;

	return (
		<SheetPrimitive
			open={open}
			onClose={guardedClose}
			ariaLabelledBy={headingId}
			initialFocus={focusPane ? paneRef : undefined}
			size="flow"
			handoffKey={AGENT_SETUP_FLOW}
		>
			<div className="flex h-full min-h-0 flex-col">
				<SheetHeader className="items-center justify-between pb-3">
					<h2
						id={headingId}
						className="font-heading text-foreground-name min-w-0 text-lg font-semibold"
					>
						Set up {total} {total === 1 ? 'API' : 'APIs'} for{' '}
						<AgentNameText name={agentName} />
					</h2>
					<Button
						variant="ghost"
						size="icon-xs"
						aria-label="Close"
						onClick={guardedClose}
						disabled={busy}
						className="shrink-0"
					>
						<X className="h-4 w-4" />
					</Button>
				</SheetHeader>

				<div className="flex min-h-0 flex-1 flex-col md:flex-row">
					<nav
						aria-label="APIs to set up"
						data-testid="queue-rail"
						// Desktop: the list scrolls in its column, so a long batch never pushes the footer.
						className="bg-surface-sheet-foot shrink-0 px-3 py-3 md:flex md:min-h-0 md:w-[248px] md:flex-col md:px-2.5"
					>
						<div className="shrink-0 px-1.5 pb-3" data-testid="queue-progress">
							<p
								className="text-foreground text-xs font-medium tabular-nums"
								role="status"
							>
								{added} of {total} added
							</p>
							<div
								className="bg-surface-tonal mt-1.5 h-1 overflow-hidden rounded-full"
								aria-hidden
							>
								<div
									className="bg-primary h-full rounded-full transition-[width] duration-500 ease-out motion-reduce:transition-none"
									style={{
										width: `${total ? ((total - summary.remaining) / total) * 100 : 0}%`,
									}}
								/>
							</div>
						</div>
						<ol
							ref={railListRef}
							aria-label="Setup progress"
							data-testid="queue-rail-list"
							// The inline padding keeps a focus ring clear of the scroll clip.
							className="-mx-1 flex gap-1 overflow-x-auto px-1 md:-mx-1 md:min-h-0 md:flex-1 md:[scrollbar-gutter:stable] md:flex-col md:gap-0.5 md:overflow-x-hidden md:overflow-y-auto md:px-1 md:py-0.5"
						>
							{entries.map((entry) => (
								<QueueRow
									key={entry.key}
									entry={entry}
									selected={entry.key === currentKey}
									disabled={busy}
									onOpen={(): void => openApi(entry.key)}
								/>
							))}
						</ol>
					</nav>

					<div
						ref={paneRef}
						tabIndex={-1}
						data-testid="queue-pane-scroll"
						className="min-h-0 flex-1 [scrollbar-gutter:stable] overflow-y-auto px-5 pt-4 pb-6 outline-none md:px-6"
					>
						{!current ? (
							<DonePane summary={summary} blocked={blocked} />
						) : (
							<section
								// Keyed, so a switch fades the next API in.
								key={current.key}
								aria-label={`Set up ${current.api.label}`}
								data-testid="queue-active-pane"
								data-status={current.status}
								className="animate-rise space-y-6"
							>
								<PaneHeader entry={current} />
								{current.status === 'failed' ? (
									<ErrorAlert
										title={`${current.api.label} wasn't added`}
										message={current.error ?? 'Something went wrong.'}
									/>
								) : settledCurrent ? (
									<AddedSummary entry={current} />
								) : (
									<>
										<CredentialSection
											entry={current}
											agentId={agentId}
											agentName={agentName}
											choice={creating ? { kind: 'new' } : choiceFor(current)}
											chosen={chosen}
											creating={creating}
											onChoose={(c): void => choose(current, c)}
											onStartCreate={(): void => {
												setCreatingKey(current.key);
												setCreatingDirty(false);
											}}
											onTouched={(): void => setCreatingDirty(true)}
											onCancelCreate={(): void => guardDraft(closeForm)}
											onCreated={(info): void => handleCreated(current, info)}
											onVendorConnected={(info): void =>
												handleVendorConnected(current, info)
											}
											onChange={(): void => setChangingKey(current.key)}
											changing={changingKey === current.key}
											onKeep={(): void => setChangingKey(null)}
										/>
										<AccessSection
											entry={current}
											chosen={chosen}
											agentName={agentName}
											draft={draft}
											onDraftChange={(patch): void =>
												setDraft(current.key, patch)
											}
											rulesIncomplete={
												rules == null && unapplied.staged === 0
											}
											reach={reach}
											reachUnknown={reachUnknown}
											sectionRef={accessRef}
											stagedRef={unapplied.stagedRef}
											onStagedChange={unapplied.onStagedChange}
											onLater={(): void => later(current)}
											importJobId={importJobs[current.key]}
										/>
									</>
								)}
							</section>
						)}
					</div>
				</div>

				<SheetFooter className="flex-nowrap justify-between gap-2 sm:gap-3">
					<div className="flex min-w-0 items-center">
						{onBack && (
							<Button variant="ghost" size="sm" onClick={goBack} disabled={busy}>
								<ArrowLeft className="h-3.5 w-3.5" />
								<span className="hidden sm:inline">Back to APIs</span>
								<span className="sm:hidden">Back</span>
							</Button>
						)}
					</div>
					<div className="flex min-w-0 items-center gap-2 sm:gap-3">
						{canSkip && (
							<Button
								variant="ghost"
								size="sm"
								onClick={(): void =>
									guardDraft(() => {
										closeForm();
										drop(current);
									})
								}
								data-testid="queue-skip"
							>
								<span className="sm:hidden">Skip</span>
								<span className="hidden sm:inline">Skip for now</span>
							</Button>
						)}
						{primary}
					</div>
				</SheetFooter>
			</div>

			<ConfirmDialog
				open={discard != null}
				title="Discard this credential?"
				body={
					<>
						What you typed for {current?.api.label ?? 'this API'} won&apos;t be saved.
						APIs already added stay added.
					</>
				}
				confirmLabel="Discard"
				onConfirm={(): void => {
					const then = discard?.then;
					setDiscard(null);
					closeForm();
					then?.();
				}}
				onClose={(): void => setDiscard(null)}
			/>
			{unapplied.dialog}
			{deviceDialog}
		</SheetPrimitive>
	);
}

/** What an added row's binding was given — custom rules counted. */
function accessLabel(entry: QueueEntry): string {
	if (entry.access === 'custom') {
		const n = entry.ruleCount ?? 0;
		return `${n} custom ${n === 1 ? 'rule' : 'rules'}`;
	}
	return entry.access ? QUEUE_ACCESS_LABELS[entry.access] : '';
}

/** One API in the list: its mark, its name, its status. Any row opens. */
function QueueRow({
	entry,
	selected,
	disabled,
	onOpen,
}: {
	entry: QueueEntry;
	selected: boolean;
	disabled: boolean;
	onOpen: () => void;
}) {
	const working = entry.status === 'working';
	const status =
		selected && !isTerminal(entry.status) && !working
			? 'In progress'
			: entry.status === 'added' && entry.access === 'later'
				? 'Added · no rules'
				: QUEUE_STATUS_LABELS[entry.status];
	const tone =
		entry.status === 'added'
			? entry.access === 'later'
				? 'text-caution'
				: 'text-success'
			: entry.status === 'failed'
				? 'text-danger'
				: entry.status === 'dropped'
					? 'text-caution'
					: selected
						? 'text-primary'
						: 'text-foreground-faint';
	return (
		<li
			data-testid="queue-progress-row"
			data-status={entry.status}
			data-selected={selected || undefined}
			className="shrink-0"
		>
			<Button
				variant="ghost"
				onClick={onOpen}
				disabled={disabled && !selected}
				aria-current={selected ? 'step' : undefined}
				className={cn(
					'h-auto w-full justify-start gap-2.5 rounded-lg px-2 py-2 text-left font-normal',
					selected ? 'bg-surface-selected hover:bg-surface-selected' : 'hover:bg-tint-2',
				)}
			>
				<VendorIcon name={entry.api.label} vendor={entry.api.vendor} size="xs" />
				<span className="min-w-0 flex-1">
					<span
						className={cn(
							'block text-sm leading-5 [overflow-wrap:anywhere]',
							selected ? 'text-foreground-name font-semibold' : 'text-foreground',
						)}
					>
						{entry.api.label}
					</span>
					<span className={cn('flex items-center gap-1.5 text-xs', tone)}>
						{entry.status === 'added' ? (
							<Check className="h-3 w-3 shrink-0" aria-hidden />
						) : entry.status === 'dropped' ? (
							<Minus className="h-3 w-3 shrink-0" aria-hidden />
						) : (
							<span
								aria-hidden
								className="h-1.5 w-1.5 shrink-0 rounded-full bg-current"
							/>
						)}
						{status}
					</span>
				</span>
			</Button>
		</li>
	);
}

function PaneHeader({ entry }: { entry: QueueEntry }) {
	const version =
		entry.api.source === 'local' && entry.api.version ? ` · v${entry.api.version}` : '';
	return (
		<div className="flex items-center gap-3">
			<VendorIcon name={entry.api.label} vendor={entry.api.vendor} size="sm" />
			<div className="min-w-0 flex-1">
				<h3 className="font-heading text-foreground-name text-sm font-semibold [overflow-wrap:anywhere]">
					{entry.api.label}
				</h3>
				<p
					data-testid="queue-active-identity"
					className="text-foreground-faint truncate font-mono text-xs"
				>
					{entry.api.vendor}
					{version}
				</p>
			</div>
		</div>
	);
}

function StepHeading({
	n,
	done,
	muted,
	children,
}: {
	n: number;
	done?: boolean;
	muted?: boolean;
	children: ReactNode;
}) {
	return (
		<h4
			className={cn(
				'mb-2.5 flex items-center gap-2 text-sm font-semibold transition-colors duration-200 ease-out motion-reduce:transition-none',
				muted ? 'text-foreground-faint' : 'text-foreground-name',
			)}
		>
			<span
				aria-hidden
				className={cn(
					'grid h-[18px] w-[18px] place-items-center rounded-full text-[10px] tabular-nums transition-colors',
					done
						? 'bg-success/15 text-success'
						: muted
							? 'bg-surface-tonal text-foreground-faint'
							: 'bg-primary/12 text-primary',
				)}
			>
				{done ? <Check className="h-3 w-3" /> : n}
			</span>
			{children}
		</h4>
	);
}

/** The chosen credential, folded to one line; "Change" reopens the choice. */
function ChosenLine({
	name,
	detail,
	onChange,
}: {
	name: string;
	detail?: string;
	onChange?: () => void;
}) {
	return (
		<p
			className="flex min-w-0 items-center gap-2 text-sm"
			data-testid="queue-chosen-credential"
		>
			<span className="text-foreground-name min-w-0 truncate font-medium">{name}</span>
			{detail && <span className="text-foreground-sub shrink-0 text-xs">· {detail}</span>}
			{onChange && (
				<Button variant="ghost" size="xs" className="ml-auto shrink-0" onClick={onChange}>
					Change
				</Button>
			)}
		</p>
	);
}

/** ① Credential: covering credentials as radios, or the create form inline. */
function CredentialSection({
	entry,
	agentId,
	agentName,
	choice,
	chosen,
	creating,
	onChoose,
	onStartCreate,
	onTouched,
	onCancelCreate,
	onCreated,
	onVendorConnected,
	onChange,
	changing,
	onKeep,
}: {
	entry: QueueEntry;
	agentId: string;
	agentName: string;
	choice: CredentialChoice | null;
	chosen: ChosenCredential | null;
	creating: boolean;
	onChoose: (choice: CredentialChoice) => void;
	onStartCreate: () => void;
	onTouched: () => void;
	onCancelCreate: () => void;
	onCreated: (info: CreatedCredentialInfo) => void;
	onVendorConnected: (info: ConnectedCredentialInfo) => void;
	onChange: () => void;
	/** "Change" reopened the chooser; the confirmed credential still stands. */
	changing: boolean;
	onKeep: () => void;
}) {
	const confirmed = entry.step === 'access' && entry.chosen ? entry.chosen : null;
	// Held through the fold, so the line doesn't blank out while it closes.
	const [lastConfirmed, setLastConfirmed] = useState(confirmed);
	if (confirmed && confirmed !== lastConfirmed) setLastConfirmed(confirmed);
	const createdCredential = useCredential(entry.created?.credential_id).data;
	const options =
		createdCredential &&
		!entry.covering.some((c) => c.credential_id === createdCredential.credential_id)
			? [createdCredential, ...entry.covering]
			: entry.covering;
	const count = options.length;
	const chooserOpen = confirmed == null || changing;
	/** While the form is open the cards fold to one line; "Show" brings them back. */
	const [listShown, setListShown] = useState(false);
	/** The form stays mounted (hidden) once opened, until it is cancelled. */
	const [formMounted, setFormMounted] = useState(creating);
	const cancelRef = useRef(false);
	if (creating && !formMounted) setFormMounted(true);
	useEffect(() => {
		if (creating) setListShown(false);
		else if (cancelRef.current) {
			cancelRef.current = false;
			setFormMounted(false);
		}
	}, [creating]);
	const formOpen = creating && !listShown;
	// No credential to pick: prebuild the form while idle; otherwise mount it on click.
	const canAddNew = count === 0 && chooserOpen && !entry.bound && entry.status !== 'working';
	useEffect(() => {
		if (formMounted || !canAddNew) return;
		const idle = window.requestIdleCallback ?? ((cb: () => void) => window.setTimeout(cb, 200));
		const cancel = window.cancelIdleCallback ?? window.clearTimeout;
		const handle = idle(() => setFormMounted(true));
		return () => cancel(handle);
	}, [formMounted, canAddNew]);
	// Heaviest subtree: built once per API, callbacks via a ref so folds skip re-rendering.
	const handlers = useRef({ onTouched, onCancelCreate, onCreated, onVendorConnected });
	useLayoutEffect(() => {
		handlers.current = { onTouched, onCancelCreate, onCreated, onVendorConnected };
	});
	const { api } = entry;
	const form = useMemo(
		() => (
			<CreateCredentialFlow
				open
				surface="inline"
				pinnedApi={api}
				preselectedAgentId={agentId}
				onTouched={(): void => {
					cancelRef.current = false;
					handlers.current.onTouched();
				}}
				onClose={(): void => {
					cancelRef.current = true;
					handlers.current.onCancelCreate();
				}}
				onCreated={(info): void => handlers.current.onCreated(info)}
				onVendorConnected={(info): void => handlers.current.onVendorConnected(info)}
			/>
		),
		[api, agentId],
	);
	const select = (next: CredentialChoice): void => {
		if (next.kind === 'new') setListShown(false);
		onChoose(next);
	};
	const existing =
		choice?.kind === 'existing'
			? (entry.covering.find((c) => c.credential_id === choice.credentialId) ?? null)
			: null;
	const other = entry.existing.length > 0 ? ' other' : '';
	return (
		<div data-testid="queue-credential-section">
			<StepHeading n={1} done={chosen != null}>
				Credential
			</StepHeading>
			<div className="space-y-3">
				{entry.existing.length > 0 && (
					<DetailsLine
						data-testid="queue-existing-accounts"
						icon={<KeyRound className="text-caution h-3.5 w-3.5" />}
						summary={`${count === 0 ? 'Needs a new key · ' : ''}${addedViaLabel(entry.existing)} already`}
					>
						<p data-testid="queue-ambiguity-warning">
							This adds a second credential; <AgentNameText name={agentName} /> keeps
							its current one. {anotherCredentialWarning(entry.api.label)} Nothing is
							stored until you save it.
						</p>
					</DetailsLine>
				)}

				{entry.bound && entry.chosen ? (
					<ChosenLine name={entry.chosen.name} detail="bound · rules not saved yet" />
				) : (
					<>
						{confirmed != null && !changing && lastConfirmed && (
							<ChosenLine
								name={lastConfirmed.name}
								detail={
									entry.created?.credential_id === lastConfirmed.credential_id
										? 'created just now'
										: undefined
								}
								onChange={entry.status === 'working' ? undefined : onChange}
							/>
						)}
						<ExpandReveal open={chooserOpen}>
							<div className="space-y-3" data-testid="queue-credential-chooser">
								{count > 0 && (
									<CredentialOptions
										id={`queue-credential-${entry.key}`}
										legend={
											count === 1
												? `Use your${other} credential for ${entry.api.label}, or add a new one`
												: `Pick one of your ${count}${other} credentials for ${entry.api.label}`
										}
										credentials={options}
										selected={creating && listShown ? null : choice}
										onSelect={select}
										newCredentialDetail="Another key or account for this API — nothing is stored until you save it"
										disabled={entry.status === 'working'}
										collapseExisting={{
											collapsed: creating && !listShown,
											summary: (
												<Button
													variant="ghost"
													size="xs"
													data-testid="queue-existing-collapsed"
													aria-expanded={false}
													onClick={(): void => setListShown(true)}
													className="w-full justify-start px-1 text-xs font-normal"
												>
													{options.length} existing credential
													{options.length === 1 ? '' : 's'}
													<span aria-hidden="true">·</span>
													<span className="text-foreground font-medium">
														Show
													</span>
													<ChevronDown
														className="h-3 w-3"
														aria-hidden="true"
													/>
												</Button>
											),
										}}
									/>
								)}
								{formMounted && (
									<ExpandReveal open={formOpen} keepMounted>
										<div
											data-testid="queue-credential-form"
											className={cn(
												count > 0 && 'bg-surface-inset rounded-lg p-4',
											)}
										>
											{form}
										</div>
									</ExpandReveal>
								)}
								{existing && credentialAwaitsConsent(existing) && (
									<p className="text-muted-foreground text-xs">
										<span className="text-foreground font-medium">
											{existing.name}
										</span>{' '}
										just needs you to finish signing in, once its access is set.
									</p>
								)}
								{count === 0 && !creating && !entry.created && (
									<Button variant="secondary" size="sm" onClick={onStartCreate}>
										Add a credential
									</Button>
								)}
								{changing && confirmed && (
									<Button
										variant="ghost"
										size="xs"
										data-testid="queue-keep-credential"
										onClick={onKeep}
									>
										Keep {confirmed.name}
									</Button>
								)}
							</div>
						</ExpandReveal>
					</>
				)}
			</div>
		</div>
	);
}

/** ② Access: muted until a credential is chosen, then the presets and their rows. */
function AccessSection({
	entry,
	chosen,
	agentName,
	draft,
	onDraftChange,
	rulesIncomplete,
	onLater,
	reach,
	reachUnknown,
	sectionRef,
	stagedRef,
	onStagedChange,
	importJobId,
}: {
	sectionRef: React.RefObject<HTMLDivElement | null>;
	/** By action's staged choices, for the queue to settle before it moves on. */
	stagedRef: React.MutableRefObject<UnappliedChoices | null>;
	onStagedChange: (count: number) => void;
	/** The catalog import the new credential queued on save, if any. */
	importJobId?: string;
	entry: QueueEntry;
	chosen: ChosenCredential | null;
	agentName: string;
	draft: AccessDraft;
	onDraftChange: (patch: Partial<AccessDraft>) => void;
	rulesIncomplete: boolean;
	onLater: () => void;
	/** How far the chosen credential's scope reaches — what Full access spans. */
	reach: ScopeReach;
	/** Reach still loading: the body stays inert so no preset commits under a wrong caption. */
	reachUnknown: boolean;
}) {
	const working = entry.status === 'working';
	const [laterPending, setLaterPending] = useState(false);
	// Focus returns to the opening row once the confirm closes.
	const laterRef = useRef<HTMLButtonElement>(null);
	// The API whose operations ② reads: the workspace API, or the one the save's
	// import registers once it lands (pending until then).
	const { reference: apiReference, pending: operationsPending } = useQueueApiReference(
		entry.api,
		{ importJobId, active: chosen != null },
	);

	return (
		<div
			ref={sectionRef}
			data-testid="queue-access-step"
			data-ready={chosen != null || undefined}
			className="scroll-mt-4"
		>
			<StepHeading n={2} muted={!chosen}>
				Access
			</StepHeading>
			{!chosen && (
				<p className="text-foreground-faint text-xs">
					Available once a credential is chosen.
				</p>
			)}
			<ExpandReveal open={chosen != null}>
				<div className="space-y-3">
					{reachUnknown && (
						<p
							className="text-foreground-faint text-xs"
							role="status"
							data-testid="queue-access-reach-pending"
						>
							Reading what this credential covers…
						</p>
					)}
					<AccessRulesStep
						reach={reach}
						preset={draft.preset}
						onPresetChange={(preset): void => onDraftChange({ preset })}
						customRules={draft.customRules}
						onCustomRulesChange={(customRules): void => onDraftChange({ customRules })}
						apiReference={apiReference}
						operationsPending={operationsPending}
						disabled={working || reachUnknown}
						heading={
							<span className="sr-only">
								What can <AgentNameText name={agentName} /> call?
							</span>
						}
						tester
						stagedRef={stagedRef}
						onStagedChange={onStagedChange}
						testIdPrefix="queue-access"
						extraTools={
							!entry.bound ? (
								// Risky, so last with an ochre fill and glyph; the label stays neutral since ochre
								// text on the ochre fill fails axe contrast (3.68:1).
								<Button
									ref={laterRef}
									variant="ghost"
									size="xs"
									disabled={working || reachUnknown}
									onClick={(): void => setLaterPending(true)}
									data-testid="queue-add-without-rules"
									className="text-foreground-sub hover:text-foreground hover:bg-warning/10 gap-1.5 px-2 text-xs font-medium"
								>
									<ShieldOff className="text-warning h-3.5 w-3.5" aria-hidden />
									Add without rules…
								</Button>
							) : undefined
						}
					/>
					{entry.rulesError && (
						<div data-testid="queue-rules-failed">
							<ErrorAlert
								title={entry.rulesError}
								message={`${entry.api.label} is added but blocked — every call is denied until its rules are saved. Your rules are kept; Save rules retries.`}
							/>
						</div>
					)}
					{rulesIncomplete && draft.preset === 'custom' && (
						<p
							className="text-muted-foreground text-xs"
							data-testid="queue-access-hint"
						>
							Add at least one rule, or pick a preset.
						</p>
					)}
					{/* Inside ② so it is found with its row; risky, not destructive. */}
					<ConfirmDialog
						open={laterPending}
						title={`Add ${entry.api.label} without rules?`}
						body={
							<>
								<AgentNameText name={agentName} /> won&apos;t be able to call{' '}
								{entry.api.label} until you add rules — every call is denied, and it
								shows as Blocked until then.
							</>
						}
						confirmLabel="Add without rules"
						destructive={false}
						pending={working}
						confirmTestId="queue-later-confirm"
						cancelLabel="Keep setting up"
						onConfirm={(): void => {
							setLaterPending(false);
							onLater();
						}}
						onClose={(): void => {
							setLaterPending(false);
							laterRef.current?.focus();
						}}
					/>
				</div>
			</ExpandReveal>
		</div>
	);
}

/** A settled API picked again from the list: what it was given. */
function AddedSummary({ entry }: { entry: QueueEntry }) {
	const blocked = entry.access === 'later';
	return (
		<div
			data-testid="queue-added-summary"
			className={cn(
				'flex items-start gap-3 rounded-lg p-4',
				blocked ? 'bg-caution/8' : 'bg-success/8',
			)}
		>
			{blocked ? (
				<AlertTriangle className="text-caution mt-0.5 h-4 w-4 shrink-0" />
			) : (
				<Check className="text-success mt-0.5 h-4 w-4 shrink-0" />
			)}
			<div className="min-w-0 text-sm">
				<p className="text-foreground">
					{blocked ? 'Added without rules — every call is denied' : 'Added'}
					{entry.credentialName && (
						<span className="text-foreground-sub"> · via {entry.credentialName}</span>
					)}
					{!blocked && entry.access && (
						<span className="text-foreground-sub"> · {accessLabel(entry)}</span>
					)}
				</p>
				{entry.note && <p className="text-muted-foreground mt-1 text-xs">{entry.note}</p>}
			</div>
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
