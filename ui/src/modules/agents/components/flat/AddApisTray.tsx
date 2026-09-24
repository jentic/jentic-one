/**
 * AddApisTray — step 1 of the Add-APIs flow: pick APIs (via the shared
 * `ApiPicker`), see what they will cost, hand the batch to the setup queue.
 *
 * Each pick is preflighted as choose-a-credential / one sign-in click /
 * needs-a-new-credential, so what the next step asks for is on screen before
 * anything commits. The rows are labels only: nothing is chosen here. A pick
 * existing credentials cover says how many, and the setup queue offers them
 * alongside "Add a new credential" — even a lone match is never reused silently.
 * The selection survives a dismissal and clears on commit or an agent change.
 *
 * The setup queue's Back re-opens the tray on its batch (`seed`): the APIs still
 * owed come back ticked and editable, the ones already added come back ticked
 * and locked — going back never undoes a saved binding.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import {
	Check,
	CircleDot,
	CirclePlus,
	LogIn,
	Plus,
	Upload,
	X,
	type LucideIcon,
} from 'lucide-react';
import { Button, ErrorAlert, LoadingState, SheetPrimitive } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import { useAllCredentials, useProviders, type SelectedApi } from '@/shared/credentials/api';
import { apiRefKey } from '@/shared/credentials/lib/apiIdentity';
import { ApiPicker } from '@/shared/credentials/components/ApiPicker';
import { ImportSpecDialog } from '@/shared/credentials/components/ImportSpecDialog';
import {
	PREFLIGHT_LABELS,
	PREFLIGHT_TALLY_ORDER,
	coveringCountLabel,
	preflightApis,
	preflightTally,
	preflightTallyLabel,
	type PreflightItem,
	type PreflightOutcome,
} from '@/modules/agents/lib/apiPreflight';
import type { CredentialBindingEntity } from '@/modules/agents/api/types';
import type { QueueBackSeed } from '@/modules/agents/lib/setupQueue';

/** Glyph and colour per outcome — a choice still to make reads as amber, a
 * sign-in as orange, and a new credential as plain work still to do. */
const OUTCOME_STYLE: Record<PreflightOutcome, { icon: LucideIcon; tone: string }> = {
	oauth: { icon: LogIn, tone: 'text-accent-orange' },
	choose: { icon: CircleDot, tone: 'text-warning' },
	form: { icon: CirclePlus, tone: 'text-muted-foreground' },
	attached: { icon: Check, tone: 'text-muted-foreground' },
};

/** The row's outcome as an icon and a short line — lighter than a pill, so the
 * API name stays the loudest thing on the row. */
function OutcomeLabel({ item }: { item: PreflightItem }) {
	const { icon: Icon, tone } = OUTCOME_STYLE[item.outcome];
	return (
		<span className={cn('inline-flex shrink-0 items-center gap-1.5 text-xs font-medium', tone)}>
			<Icon className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
			{item.outcome === 'attached' && item.attachedVia
				? `Already added via ${item.attachedVia}`
				: PREFLIGHT_LABELS[item.outcome]}
		</span>
	);
}

export interface AddApisTrayProps {
	open: boolean;
	onClose: () => void;
	/** The agent the picks are for. Doubles as the selection's reset key. */
	agentId: string;
	agentName: string;
	/** The agent's existing bindings — they say which APIs it already reaches. */
	bindings: CredentialBindingEntity[];
	/** Hand the preflighted batch on — actionable items only, in pick order. While
	 * editing a batch (`seed`), this may be empty: every owed API was unticked. */
	onContinue: (items: PreflightItem[]) => void;
	/** The setup queue's batch, when the operator went Back to edit it. A new seed
	 * replaces the draft; `null` means the tray is not editing a batch. */
	seed?: QueueBackSeed | null;
}

export function AddApisTray({
	open,
	onClose,
	agentId,
	agentName,
	bindings,
	onContinue,
	seed = null,
}: AddApisTrayProps) {
	const headingId = 'add-apis-tray-title';
	const [picks, setPicks] = useState<SelectedApi[]>(() => seed?.picks ?? []);
	/** APIs this batch already added — shown ticked and locked, never re-queued. */
	const [locked, setLocked] = useState<SelectedApi[]>(() => seed?.added ?? []);
	/** Spec upload. First-class here because "the API I need isn't in the catalog"
	 * is otherwise a dead end mid-flow; a successful import lands in the selection,
	 * so the operator never has to search for what they just uploaded. */
	const [uploadOpen, setUploadOpen] = useState(false);
	/** Where focus lands on every open — including a re-open from the queue's Back,
	 * which can catch the sheet mid-exit with the picker still mounted. */
	const searchRef = useRef<HTMLInputElement>(null);

	// The draft belongs to ONE agent, so it resets when the agent changes — never
	// on an `open` flip, which would discard picks a dismissal must preserve.
	const lastAgentIdRef = useRef(agentId);
	useEffect(() => {
		if (lastAgentIdRef.current !== agentId) {
			lastAgentIdRef.current = agentId;
			setPicks([]);
			setLocked([]);
		}
	}, [agentId]);

	// Seed-from-props syncs only when the seed itself changes, never on `open`. The
	// draft is a view of the queue's batch, so a seed withdrawn without a commit
	// (the tray was closed; the batch waits in the queue) clears it too.
	const lastSeedRef = useRef(seed);
	useEffect(() => {
		if (lastSeedRef.current === seed) return;
		lastSeedRef.current = seed;
		setPicks(seed?.picks ?? []);
		setLocked(seed?.added ?? []);
	}, [seed]);
	const editingBatch = seed != null;

	// Preflight reads the WHOLE credential list: a first-page-only list would call
	// an existing credential "needs a new credential" and hide it from the choice.
	const credentialsSource = useAllCredentials();
	const providersQuery = useProviders();
	const managedOAuthAvailable = useMemo(
		() => (providersQuery.data?.providers ?? []).some((p) => p.managed && p.configured),
		[providersQuery.data],
	);

	const items = useMemo(
		() =>
			preflightApis(picks, {
				credentials: credentialsSource.items,
				bindings,
				managedOAuthAvailable,
			}),
		[picks, credentialsSource.items, bindings, managedOAuthAvailable],
	);
	const tally = useMemo(() => preflightTally(items), [items]);

	const lockedKeys = useMemo(() => new Set(locked.map(apiRefKey)), [locked]);
	// Locked rows read ticked (they are in the batch) as well as disabled.
	const selectedKeys = useMemo(
		() => new Set([...picks.map(apiRefKey), ...lockedKeys]),
		[picks, lockedKeys],
	);

	// Rows the agent already reaches, so they render as "Already added" instead of
	// inviting a duplicate bind. Only bindings naming a concrete API are
	// enumerable; a vendor wildcard is caught by the preflight's `attached`.
	const attachedKeys = useMemo(() => {
		const keys = new Set<string>();
		for (const binding of bindings) {
			for (const served of binding.serves) {
				if (served.name != null)
					keys.add(apiRefKey({ vendor: served.vendor, name: served.name }));
			}
		}
		return keys;
	}, [bindings]);
	const disabledKeys = useMemo(
		() => new Set([...attachedKeys, ...lockedKeys]),
		[attachedKeys, lockedKeys],
	);

	const remove = (key: string): void =>
		setPicks((current) => current.filter((p) => apiRefKey(p) !== key));

	const toggle = (api: SelectedApi): void => {
		const key = apiRefKey(api);
		if (picks.some((p) => apiRefKey(p) === key)) remove(key);
		else setPicks((current) => [...current, api]);
	};

	// Append, never toggle: re-uploading an API already picked must not drop it.
	const addImported = (apis: SelectedApi[]): void =>
		setPicks((current) => {
			const keys = new Set([...current.map(apiRefKey), ...lockedKeys]);
			return [...current, ...apis.filter((api) => !keys.has(apiRefKey(api)))];
		});

	const preflightReady = credentialsSource.complete && !credentialsSource.error;
	// Editing a batch may end with nothing left to set up — that is still an answer.
	const canContinue = preflightReady && (tally.actionable > 0 || editingBatch);

	const handleContinue = (): void => {
		const actionable = items.filter((item) => item.outcome !== 'attached');
		if (actionable.length === 0 && !editingBatch) return;
		onContinue(actionable);
		// Reset on commit — the queue owns these picks now.
		setPicks([]);
		setLocked([]);
	};

	return (
		<SheetPrimitive
			open={open}
			onClose={onClose}
			ariaLabelledBy={headingId}
			initialFocus={searchRef}
			className="sm:w-[640px] xl:w-[760px]"
		>
			<div className="flex h-full flex-col">
				<header className="border-border flex items-start justify-between gap-3 border-b px-5 py-4">
					<div className="min-w-0">
						<h2 id={headingId} className="text-foreground text-base font-semibold">
							Add APIs
						</h2>
						{/* Stated up front, not discovered at the end: an API with no credential
						    has nowhere to be stored. */}
						<p className="text-muted-foreground text-xs">
							Pick what {agentName} should be able to call. Each API gets a credential
							in this flow — nothing is set up later.
						</p>
					</div>
					<Button
						variant="ghost"
						size="sm"
						aria-label="Close"
						onClick={onClose}
						className="text-muted-foreground hover:text-foreground shrink-0"
					>
						<X className="h-4 w-4" />
					</Button>
				</header>

				<div className="flex-1 overflow-y-auto px-5 py-4">
					<ApiPicker
						searchInputRef={searchRef}
						onSelect={toggle}
						selectedKeys={selectedKeys}
						disabledKeys={disabledKeys}
						disabledLabel={(key): string =>
							lockedKeys.has(key) ? 'Added' : 'Already added'
						}
						emptyAction={
							<Button
								variant="secondary"
								size="sm"
								onClick={(): void => setUploadOpen(true)}
							>
								<Upload className="h-4 w-4" />
								Upload an API
							</Button>
						}
					/>
				</div>

				{(picks.length > 0 || locked.length > 0) && (
					<section
						aria-label="Selected APIs"
						className="border-border bg-muted/20 border-t px-5 py-3"
					>
						<ul className="divide-border/50 mb-3 max-h-72 divide-y overflow-y-auto">
							{/* Already added by this batch: a record, not a choice — no remove. */}
							{locked.map((api) => (
								<li
									key={apiRefKey(api)}
									data-testid="tray-selection-added"
									className="py-1.5"
								>
									<div className="flex items-center gap-2 text-sm">
										<span className="text-foreground min-w-0 flex-1 truncate">
											{api.label}
										</span>
										<span className="text-success inline-flex shrink-0 items-center gap-1.5 text-xs font-medium">
											<Check
												className="h-3.5 w-3.5 shrink-0"
												aria-hidden="true"
											/>
											Added
										</span>
									</div>
								</li>
							))}
							{items.map((item) => (
								<li key={item.key} data-testid="tray-selection" className="py-1.5">
									<div className="flex items-center gap-2 text-sm">
										<span className="text-foreground min-w-0 flex-1 truncate">
											{item.api.label}
										</span>
										<OutcomeLabel item={item} />
										<Button
											variant="ghost"
											size="sm"
											aria-label={`Remove ${item.api.label}`}
											onClick={(): void => remove(item.key)}
											className="text-muted-foreground hover:text-foreground shrink-0"
										>
											<X className="h-3.5 w-3.5" />
										</Button>
									</div>
									{item.outcome === 'choose' && (
										<p
											data-testid="tray-covering-count"
											className="text-muted-foreground mt-0.5 truncate text-xs"
										>
											{coveringCountLabel(item.covering.length)}
										</p>
									)}
								</li>
							))}
						</ul>

						{credentialsSource.error ? (
							<ErrorAlert
								message="Could not read your credentials, so the cost of these picks is unknown."
								onRetry={credentialsSource.retry}
							/>
						) : !credentialsSource.complete ? (
							<LoadingState message="Checking which credentials you already have…" />
						) : (
							<TallyLines tally={tally} />
						)}
					</section>
				)}

				<footer className="border-border flex flex-wrap items-center justify-between gap-3 border-t px-5 py-3">
					<div className="flex min-w-0 items-center gap-3">
						<p className="text-muted-foreground text-xs">
							{picks.length === 0 && locked.length === 0
								? 'Nothing selected yet.'
								: [
										`${picks.length} selected`,
										tally.attached > 0 && `${tally.attached} already added`,
										locked.length > 0 && `${locked.length} added so far`,
									]
										.filter(Boolean)
										.join(', ')}
						</p>
						{/* Always reachable, not only from no-results: an operator who knows the
						    API isn't catalogued shouldn't have to search first. */}
						<Button
							variant="ghost"
							size="sm"
							onClick={(): void => setUploadOpen(true)}
							className="text-muted-foreground hover:text-foreground"
						>
							<Upload className="h-3.5 w-3.5" />
							Upload an API
						</Button>
					</div>
					<div className="flex shrink-0 items-center gap-2">
						<Button variant="ghost" size="sm" onClick={onClose}>
							Cancel
						</Button>
						{/* Every pick stops in the next step, so the button promises
						    exactly that — nothing is bound from here. */}
						<Button size="sm" disabled={!canContinue} onClick={handleContinue}>
							<Plus className="h-4 w-4" />
							{/* Editing a batch down to nothing left to set up just finishes. */}
							{editingBatch && tally.actionable === 0 ? 'Done' : 'Continue'}
						</Button>
					</div>
				</footer>
			</div>

			{/* Inside the sheet, like the queue's credential wizard: a native
			    `<dialog>` renders in the top layer, over the tray that owns the picks. */}
			<ImportSpecDialog
				open={uploadOpen}
				onClose={(): void => setUploadOpen(false)}
				onImported={addImported}
			/>
		</SheetPrimitive>
	);
}

/** What the next step will ask for, per outcome — zero-count lines omitted. */
function TallyLines({ tally }: { tally: ReturnType<typeof preflightTally> }) {
	const lines = PREFLIGHT_TALLY_ORDER.filter((outcome) => tally[outcome] > 0);
	return (
		<div className="space-y-1">
			{lines.map((outcome) => (
				<p
					key={outcome}
					data-testid="tray-tally-line"
					className="text-muted-foreground text-xs"
				>
					{preflightTallyLabel(outcome, tally[outcome])}
				</p>
			))}
			{tally.imports > 0 && (
				<p className="text-muted-foreground/80 text-xs">
					{tally.imports === 1
						? '1 API will be imported into your Workspace.'
						: `${tally.imports} APIs will be imported into your Workspace.`}
				</p>
			)}
		</div>
	);
}
