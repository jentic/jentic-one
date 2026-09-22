/**
 * AddApisTray — step 1 of the Add-APIs flow: pick APIs (via the shared
 * `ApiPicker`), see what they will cost, hand the batch to the setup queue.
 *
 * Each pick is preflighted as reuse / one sign-in click / pick-which-credential /
 * needs-a-new-credential, so the cost is on screen before anything commits. The
 * selection survives a dismissal and clears on commit or an agent change.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { Plus, Upload, X } from 'lucide-react';
import { Badge, Button, ErrorAlert, LoadingState, SheetPrimitive } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import { useAllCredentials, useProviders, type SelectedApi } from '@/shared/credentials/api';
import { apiRefKey } from '@/shared/credentials/lib/apiIdentity';
import { ApiPicker } from '@/shared/credentials/components/ApiPicker';
import { ImportSpecDialog } from '@/shared/credentials/components/ImportSpecDialog';
import {
	PREFLIGHT_LABELS,
	PREFLIGHT_TALLY_ORDER,
	preflightApis,
	preflightTally,
	preflightTallyLabel,
	type PreflightItem,
	type PreflightOutcome,
} from '@/modules/agents/lib/apiPreflight';
import type { CredentialBindingEntity } from '@/modules/agents/api/types';

/** Badge colour per outcome — cheapest reads as success, costliest as neutral. */
const OUTCOME_VARIANT: Record<PreflightOutcome, 'default' | 'success' | 'warning' | 'pending'> = {
	reuse: 'success',
	oauth: 'pending',
	choose: 'warning',
	form: 'default',
	attached: 'default',
};

export interface AddApisTrayProps {
	open: boolean;
	onClose: () => void;
	/** The agent the picks are for. Doubles as the selection's reset key. */
	agentId: string;
	agentName: string;
	/** The agent's existing bindings — they say which APIs it already reaches. */
	bindings: CredentialBindingEntity[];
	/** Hand the preflighted batch on — actionable items only, in pick order. */
	onContinue: (items: PreflightItem[]) => void;
}

export function AddApisTray({
	open,
	onClose,
	agentId,
	agentName,
	bindings,
	onContinue,
}: AddApisTrayProps) {
	const headingId = 'add-apis-tray-title';
	const [picks, setPicks] = useState<SelectedApi[]>([]);
	/** Spec upload. First-class here because "the API I need isn't in the catalog"
	 * is otherwise a dead end mid-flow; a successful import invalidates the picker's
	 * list, so the new API is searchable without leaving the tray. */
	const [uploadOpen, setUploadOpen] = useState(false);

	// The draft belongs to ONE agent, so it resets when the agent changes — never
	// on an `open` flip, which would discard picks a dismissal must preserve.
	const lastAgentIdRef = useRef(agentId);
	useEffect(() => {
		if (lastAgentIdRef.current !== agentId) {
			lastAgentIdRef.current = agentId;
			setPicks([]);
		}
	}, [agentId]);

	// Preflight reads the WHOLE credential list: a first-page-only list would call
	// an existing credential "needs a new credential" and turn reuse into a form.
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

	const selectedKeys = useMemo(() => new Set(picks.map(apiRefKey)), [picks]);

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

	const toggle = (api: SelectedApi): void => {
		const key = apiRefKey(api);
		setPicks((current) =>
			current.some((p) => apiRefKey(p) === key)
				? current.filter((p) => apiRefKey(p) !== key)
				: [...current, api],
		);
	};

	const remove = (key: string): void =>
		setPicks((current) => current.filter((p) => apiRefKey(p) !== key));

	const preflightReady = credentialsSource.complete && !credentialsSource.error;
	const canContinue = preflightReady && tally.actionable > 0;

	const handleContinue = (): void => {
		const actionable = items.filter((item) => item.outcome !== 'attached');
		if (actionable.length === 0) return;
		onContinue(actionable);
		// Reset on commit — the queue owns these picks now.
		setPicks([]);
	};

	return (
		<SheetPrimitive
			open={open}
			onClose={onClose}
			ariaLabelledBy={headingId}
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
						onSelect={toggle}
						selectedKeys={selectedKeys}
						disabledKeys={attachedKeys}
						disabledLabel="Already added"
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

				{picks.length > 0 && (
					<section
						aria-label="Selected APIs"
						className="border-border bg-muted/20 border-t px-5 py-3"
					>
						<ul className="mb-3 max-h-40 space-y-1 overflow-y-auto">
							{items.map((item) => (
								<li
									key={item.key}
									data-testid="tray-selection"
									className="flex items-center gap-2 text-sm"
								>
									<span className="text-foreground min-w-0 flex-1 truncate">
										{item.api.label}
									</span>
									<Badge
										variant={OUTCOME_VARIANT[item.outcome]}
										className="shrink-0 text-[10px]"
									>
										{item.outcome === 'attached' && item.attachedVia
											? `Already added via ${item.attachedVia}`
											: PREFLIGHT_LABELS[item.outcome]}
									</Badge>
									<Button
										variant="ghost"
										size="sm"
										aria-label={`Remove ${item.api.label}`}
										onClick={(): void => remove(item.key)}
										className="text-muted-foreground hover:text-foreground shrink-0"
									>
										<X className="h-3.5 w-3.5" />
									</Button>
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
							{picks.length === 0
								? 'Nothing selected yet.'
								: `${picks.length} selected${tally.attached > 0 ? `, ${tally.attached} already added` : ''}`}
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
						<Button size="sm" disabled={!canContinue} onClick={handleContinue}>
							<Plus className="h-4 w-4" />
							{/* When nothing needs a queue stop, the next click IS the
							    whole job — say so rather than promising more steps. */}
							{tally.actionable > 0 && tally.queued === 0
								? `Add ${tally.actionable} ${tally.actionable === 1 ? 'API' : 'APIs'}`
								: 'Continue'}
						</Button>
					</div>
				</footer>
			</div>

			{/* Inside the sheet, like the queue's credential wizard: a native
			    `<dialog>` renders in the top layer, over the tray that owns the picks. */}
			<ImportSpecDialog open={uploadOpen} onClose={(): void => setUploadOpen(false)} />
		</SheetPrimitive>
	);
}

/** The cost breakdown — cheapest line first, zero-count lines omitted. */
function TallyLines({ tally }: { tally: ReturnType<typeof preflightTally> }) {
	const lines = PREFLIGHT_TALLY_ORDER.filter((outcome) => tally[outcome] > 0);
	return (
		<div className="space-y-1">
			{lines.map((outcome) => (
				<p
					key={outcome}
					data-testid="tray-tally-line"
					className={cn(
						'text-xs',
						outcome === 'reuse' ? 'text-success' : 'text-muted-foreground',
					)}
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
