/**
 * AddApisTray — step 1 of the Add-APIs flow: pick APIs (via the shared
 * `ApiPicker`), hand the batch to the setup queue.
 *
 * Each pick is preflighted (counting only credentials the operator may bind —
 * `credentialsBindableBy`); a lone covering match is never reused silently.
 * An API the agent already reaches stays pickable: an agent may hold several
 * credentials for one API, so its row says which it has and the pick adds another.
 * The selection survives a dismissal and clears on commit or an agent change.
 *
 * A folded line above the footer records what an earlier pass committed and which
 * picks would add a second credential for an API the agent already reaches.
 *
 * The setup queue's Back re-opens the tray on its batch (`seed`): the APIs still
 * owed come back ticked and editable, the ones already added come back ticked
 * and locked — going back never undoes a saved binding.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowRight, Check, Upload, X } from 'lucide-react';
import {
	Button,
	DetailsLine,
	ErrorAlert,
	SheetBody,
	SheetFooter,
	SheetHeader,
	SheetPrimitive,
} from '@/shared/ui';
import { useNoAuthPicks, type SelectedApi, type VendorSummary } from '@/shared/credentials/api';
import { apiRefKey } from '@/shared/credentials/lib/apiIdentity';
import { ApiPicker, ImportSpecDialog, CreateCredentialFlow } from '@/shared/credentials/components';
import {
	addedViaLabel,
	preflightApis,
	type PreflightItem,
} from '@/modules/agents/lib/apiPreflight';
import type { CredentialBindingEntity } from '@/modules/agents/api/types';
import { AGENT_SETUP_FLOW, type QueueBackSeed } from '@/modules/agents/lib/setupQueue';
import { usePreflightInputs } from '@/modules/agents/lib/usePreflightInputs';
import { AgentNameText } from '@/modules/agents/components/AgentNameText';

/** The folded record's one line: what is already in, and what would double up. */
function recordSummary(added: number, duplicating: number): string {
	const parts = [
		added > 0 && `${added} already added`,
		duplicating > 0 &&
			`${duplicating} ${duplicating === 1 ? 'pick adds' : 'picks add'} another credential`,
	].filter(Boolean);
	return parts.join(' · ');
}

export interface AddApisTrayProps {
	open: boolean;
	onClose: () => void;
	/** The agent the picks are for. Doubles as the selection's reset key. */
	agentId: string;
	agentName: string;
	/** The agent's existing bindings — they say which APIs it already reaches. */
	bindings: CredentialBindingEntity[];
	/** The bindings read failed. Without them every credential the agent holds
	 * looks unbound and would be offered again (a 409 on bind), so the tray shows
	 * the error with a retry and nothing continues until the read succeeds. */
	bindingsError?: Error | null;
	/** Re-read the bindings — the retry for {@link bindingsError}. */
	onRetryBindings?: () => void;
	/** A re-read of the bindings is in flight. */
	bindingsRetrying?: boolean;
	/** Hand the preflighted batch on, in pick order. While
	 * editing a batch (`seed`), this may be empty: every owed API was unticked. */
	onContinue: (items: PreflightItem[]) => void;
	/** The setup queue's batch, when the operator went Back to edit it. A new seed
	 * replaces the draft; `null` means the tray is not editing a batch. */
	seed?: QueueBackSeed | null;
	/** Opened from the New agent panel: the way out reads "Skip for now". */
	fromNewAgent?: boolean;
}

export function AddApisTray({
	open,
	onClose,
	agentId,
	agentName,
	bindings,
	bindingsError = null,
	onRetryBindings,
	bindingsRetrying = false,
	onContinue,
	seed = null,
	fromNewAgent = false,
}: AddApisTrayProps) {
	const headingId = 'add-apis-tray-title';
	const [picks, setPicks] = useState<SelectedApi[]>(() => seed?.picks ?? []);
	/** APIs this batch already added — shown ticked and locked, never re-queued. */
	const [locked, setLocked] = useState<SelectedApi[]>(() => seed?.added ?? []);
	/** Spec upload. First-class here because "the API I need isn't in the catalog"
	 * is otherwise a dead end mid-flow; a successful import lands in the selection,
	 * so the operator never has to search for what they just uploaded. */
	const [uploadOpen, setUploadOpen] = useState(false);
	/** A one-click sign-in picked from the picker (e.g. a shared OAuth app): it
	 * connects a credential straight onto this agent, outside the API batch. */
	const [vendorPick, setVendorPick] = useState<VendorSummary | null>(null);
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

	const { inputs: baseInputs, credentialsSource } = usePreflightInputs(bindings);
	// Each pick's spec is read as it's ticked, so a no-auth API says so here —
	// before the setup queue — instead of reading "Needs a new credential".
	const noAuthKeys = useNoAuthPicks(picks);
	const preflightInputs = useMemo(
		() => ({ ...baseInputs, noAuthKeys }),
		[baseInputs, noAuthKeys],
	);
	const items = useMemo(() => preflightApis(picks, preflightInputs), [picks, preflightInputs]);

	const lockedKeys = useMemo(() => new Set(locked.map(apiRefKey)), [locked]);
	// Locked rows read ticked (they are in the batch) as well as disabled.
	const selectedKeys = useMemo(
		() => new Set([...picks.map(apiRefKey), ...lockedKeys]),
		[picks, lockedKeys],
	);

	// Rows the agent already reaches say through which credentials, and stay
	// pickable to add another credential. Only bindings naming a concrete API are
	// enumerable; a vendor wildcard is named by the preflight once picked.
	const addedVia = useMemo(() => {
		const names = new Map<string, string[]>();
		for (const binding of bindings) {
			for (const served of binding.serves) {
				if (served.name == null) continue;
				const key = apiRefKey({ vendor: served.vendor, name: served.name });
				const label = binding.name || binding.credentialId;
				names.set(key, [...(names.get(key) ?? []), label]);
			}
		}
		return names;
	}, [bindings]);
	const rowHint = (key: string): string | undefined => {
		const names = addedVia.get(key);
		return names ? `Added via ${names.join(', ')} · add another credential` : undefined;
	};

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

	const preflightReady = credentialsSource.complete && !credentialsSource.error && !bindingsError;
	// Editing a batch may end with nothing left to set up — that is still an answer.
	const canContinue = preflightReady && (items.length > 0 || editingBatch);
	// Continue waits on the credential read, so a selection made meanwhile says so.
	const waitingOnCredentials =
		!credentialsSource.complete &&
		!credentialsSource.error &&
		!bindingsError &&
		(picks.length > 0 || locked.length > 0);

	const handleContinue = (): void => {
		if (items.length === 0 && !editingBatch) return;
		onContinue(items);
		// Reset on commit — the queue owns these picks now.
		setPicks([]);
		setLocked([]);
	};

	const duplicating = useMemo(() => items.filter((i) => i.existing.length > 0), [items]);
	const recordShown = locked.length > 0 || duplicating.length > 0;

	return (
		<SheetPrimitive
			open={open}
			// The sign-in stacks as a second sheet and both see the same Escape —
			// dismissing it must leave the operator in the tray.
			onClose={(): void => {
				if (!vendorPick) onClose();
			}}
			ariaLabelledBy={headingId}
			initialFocus={searchRef}
			size="flow"
			handoffKey={AGENT_SETUP_FLOW}
		>
			<div className="flex h-full flex-col">
				<SheetHeader className="justify-between">
					<div className="min-w-0">
						<h2
							id={headingId}
							className="font-heading text-foreground-name text-lg font-semibold"
						>
							Add APIs
						</h2>
						<p className="text-muted-foreground text-xs">
							Pick what <AgentNameText name={agentName} /> should be able to call.
						</p>
					</div>
					<Button
						variant="ghost"
						size="icon-xs"
						aria-label="Close"
						onClick={onClose}
						className="shrink-0"
					>
						<X className="h-4 w-4" />
					</Button>
				</SheetHeader>

				<SheetBody>
					{bindingsError && (
						<ErrorAlert
							className="mb-4"
							message={`Could not read which APIs ${agentName} already has, so nothing can be added yet.`}
							onRetry={onRetryBindings}
							retrying={bindingsRetrying}
						/>
					)}
					<ApiPicker
						searchInputRef={searchRef}
						onSelect={toggle}
						onVendorSelect={setVendorPick}
						selectedKeys={selectedKeys}
						disabledKeys={lockedKeys}
						disabledLabel="Added"
						rowHint={rowHint}
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
				</SheetBody>

				{/* Only a failed credentials read (which Continue waits on) needs saying here. */}
				{!bindingsError && credentialsSource.error && (
					<div className="shrink-0 px-5 pb-3">
						<ErrorAlert
							message="Could not read your credentials, so these picks can't continue yet."
							onRetry={credentialsSource.retry}
						/>
					</div>
				)}

				{!bindingsError && recordShown && (
					<DetailsLine
						data-testid="tray-record"
						className="shrink-0 px-5 pb-3"
						summary={recordSummary(locked.length, duplicating.length)}
					>
						<ul className="grid gap-1">
							{/* Already added by an earlier pass: a record, not a choice. */}
							{locked.map((api) => (
								<li
									key={apiRefKey(api)}
									data-testid="tray-selection-added"
									className="flex items-center gap-2"
								>
									<span className="text-foreground min-w-0 flex-1 truncate">
										{api.label}
									</span>
									<span className="text-success inline-flex shrink-0 items-center gap-1.5 font-medium">
										<Check
											aria-hidden="true"
											className="h-3.5 w-3.5 shrink-0"
										/>
										Added
									</span>
								</li>
							))}
							{duplicating.map((item) => (
								<li key={item.key} className="min-w-0">
									<span className="text-foreground block truncate">
										{item.api.label}
									</span>
									<span data-testid="tray-added-via" className="block truncate">
										{addedViaLabel(item.existing)} — this adds another
										credential
									</span>
								</li>
							))}
						</ul>
					</DetailsLine>
				)}

				<SheetFooter className="flex-wrap justify-between gap-3">
					<div className="flex min-w-0 items-center gap-3">
						<p
							className="text-muted-foreground text-xs"
							data-testid="tray-selection-note"
						>
							{waitingOnCredentials
								? 'Checking which credentials you already have…'
								: picks.length === 0 && locked.length === 0
									? 'Nothing selected yet.'
									: [
											`${picks.length} selected`,
											locked.length > 0 && `${locked.length} added so far`,
										]
											.filter(Boolean)
											.join(', ')}
						</p>
						{/* Always reachable, not only from no-results: an operator who knows the
						    API isn't catalogued shouldn't have to search first. */}
						<Button variant="ghost" size="sm" onClick={(): void => setUploadOpen(true)}>
							<Upload className="h-3.5 w-3.5" />
							Upload an API
						</Button>
					</div>
					<div className="flex shrink-0 items-center gap-2">
						<Button variant="ghost" size="sm" onClick={onClose}>
							{fromNewAgent ? 'Skip for now' : 'Cancel'}
						</Button>
						{/* Every pick stops in the next step, so the button promises
						    exactly that — nothing is bound from here. */}
						<Button
							size="sm"
							disabled={!canContinue}
							loading={waitingOnCredentials}
							onClick={handleContinue}
						>
							{/* Editing a batch down to nothing left to set up just finishes. */}
							{editingBatch && items.length === 0 ? (
								'Done'
							) : (
								<>
									Continue
									<ArrowRight className="h-4 w-4" />
								</>
							)}
						</Button>
					</div>
				</SheetFooter>
			</div>

			{/* Inside the sheet, like the queue's credential wizard: a native
			    `<dialog>` renders in the top layer, over the tray that owns the picks. */}
			<ImportSpecDialog
				open={uploadOpen}
				onClose={(): void => setUploadOpen(false)}
				onImported={addImported}
			/>
			{vendorPick && (
				<CreateCredentialFlow
					key={vendorPick.entry_id}
					open
					initialVendor={vendorPick}
					preselectedAgentId={agentId}
					onClose={(): void => setVendorPick(null)}
					onCreated={(): void => setVendorPick(null)}
				/>
			)}
		</SheetPrimitive>
	);
}
