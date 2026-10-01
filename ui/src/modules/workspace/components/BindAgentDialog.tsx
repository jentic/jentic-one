/**
 * BindAgentDialog — "Bind to an agent", in place on the API hub's "Who can use
 * it" card: pick one of this API's credentials (when there are several), tick
 * existing agents, confirm.
 *
 * Reuses the shared binding path the post-connect "Bind to more agents" CTA
 * uses (`useAgentsForPicker` + `useBindCredentialToAgents` →
 * `POST /agents/{id}/credentials`, one per agent, in "start blocked" mode: no
 * permission rules). On success it closes with a "Credential bound" toast —
 * the app's pattern for credential create / edit / bind — and the agent shows
 * up in the "Who can use it" card, linked to its page.
 *
 * Offered only to a viewer with `agents:write` (the bind endpoint's
 * permission). The credential list is narrowed to the ones the viewer may bind
 * — the server lets a non-admin bind only credentials they created (else 404).
 * Agents already bound to the chosen credential are shown, disabled — read in
 * full (every page, `useAllCredentialAgents`), so an agent past the first page
 * can't pass for unbound and be bound twice. Until that read is whole, nothing
 * can be ticked or bound.
 *
 * State lifecycle (dialog-state rule): the draft (credential + ticks) persists
 * across dismissals and resets on a successful bind; transient error clears on
 * reopen.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { Bot } from 'lucide-react';
import {
	ActorStatusBadge,
	AppLink,
	Button,
	Checkbox,
	Dialog,
	EmptyState,
	ErrorAlert,
	Label,
	Select,
	Skeleton,
	toActorStatus,
	toast,
} from '@/shared/ui';
import { ROUTES } from '@/shared/app/routes';
import { useOptionalCurrentUser } from '@/shared/auth';
import { credentialsBindableBy } from '@/shared/credentials/lib/bindAuthority';
import { credentialSiblingHint } from '@/shared/credentials/lib/credentialIdentity';
import { useAllCredentialAgents, type Credential } from '@/shared/credentials/api';
import {
	useAgentsForPicker,
	useBindCredentialToAgents,
} from '@/shared/credentials/api/vendors-hooks';

/** Lifecycle states an agent can't usefully be granted access in. */
const UNBINDABLE_STATUSES = new Set(['archived', 'rejected']);

export interface BindAgentDialogProps {
	open: boolean;
	onClose: () => void;
	/** Every credential whose scope covers this API. */
	credentials: Credential[];
	/** Shown in the subtitle, e.g. "NewsAPI". */
	apiLabel: string;
	/**
	 * Preselect this credential. Seeds the pick when it changes (a caller that
	 * just created a credential opens the dialog on it); the user can still
	 * switch.
	 */
	initialCredentialId?: string | null;
}

/** "a", "a and b", "a, b and c"; past three, just the count. */
function agentList(names: string[]): string {
	if (names.length > 3) return `${names.length} agents`;
	if (names.length <= 1) return names[0] ?? '';
	return `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;
}

export function BindAgentDialog({
	open,
	onClose,
	credentials,
	apiLabel,
	initialCredentialId,
}: BindAgentDialogProps) {
	const viewer = useOptionalCurrentUser();
	const usable = useMemo(() => credentialsBindableBy(credentials, viewer), [credentials, viewer]);
	const agents = useAgentsForPicker();
	const bind = useBindCredentialToAgents();

	const [credentialId, setCredentialId] = useState<string | null>(initialCredentialId ?? null);
	// Seed-from-props syncs only when the seed itself changes (dialog-state rule).
	const lastSeedRef = useRef(initialCredentialId);
	useEffect(() => {
		if (lastSeedRef.current !== initialCredentialId) {
			lastSeedRef.current = initialCredentialId;
			if (initialCredentialId) setCredentialId(initialCredentialId);
		}
	}, [initialCredentialId]);
	const [selected, setSelected] = useState<Set<string>>(new Set());

	// Transient: clear the last error on every (re)open.
	useEffect(() => {
		if (open) bind.reset();
		// eslint-disable-next-line react-hooks/exhaustive-deps -- reset is stable enough; only `open` should retrigger
	}, [open]);

	// A stale / unusable pick falls back to the first usable credential.
	const credential = usable.find((c) => c.credential_id === credentialId) ?? usable[0] ?? null;
	// Every page: a partial list would offer already-bound agents as unbound.
	const boundHere = useAllCredentialAgents(credential?.credential_id, {
		enabled: open && credential != null,
	});
	const boundKnown = boundHere.complete;
	const alreadyBound = useMemo(
		() => new Set(boundHere.items.map((a) => a.agent_id)),
		[boundHere.items],
	);

	const candidates = useMemo(
		() => (agents.data?.data ?? []).filter((a) => !UNBINDABLE_STATUSES.has(a.status)),
		[agents.data],
	);
	// Ticks only count for agents still bindable to the chosen credential.
	const picked = boundKnown
		? candidates.filter((a) => selected.has(a.id) && !alreadyBound.has(a.id))
		: [];

	const toggle = (id: string): void =>
		setSelected((prev) => {
			const next = new Set(prev);
			if (next.has(id)) next.delete(id);
			else next.add(id);
			return next;
		});

	const handleBind = (): void => {
		if (!credential || picked.length === 0) return;
		bind.mutate(
			{ credentialId: credential.credential_id, agentIds: picked.map((a) => a.id) },
			{
				onSuccess: () => {
					toast({
						title: 'Credential bound',
						description: `Bound “${credential.name}” to ${agentList(picked.map((a) => a.name))}.`,
						variant: 'success',
					});
					// Reset the draft only on a successful commit.
					setSelected(new Set());
					onClose();
				},
			},
		);
	};

	const noAgents = !agents.isPending && !agents.error && candidates.length === 0;

	let body: React.ReactNode;
	if (usable.length === 0) {
		body = (
			<p className="text-muted-foreground text-sm" data-testid="bind-agent-no-credential">
				You can only bind credentials you created, and none of this API&apos;s credentials
				are yours. Add your own credential, or ask an admin to bind one.
			</p>
		);
	} else if (agents.isPending) {
		body = <Skeleton className="h-24 w-full" />;
	} else if (agents.error) {
		body = <ErrorAlert message={agents.error} />;
	} else if (noAgents) {
		body = (
			<EmptyState
				icon={<Bot className="h-6 w-6" aria-hidden="true" />}
				title="No agents yet"
				description="Create or register an agent first, then bind this API's credential to it."
				action={
					<AppLink
						href={ROUTES.agents}
						variant="outline"
						size="sm"
						onClick={onClose}
						data-testid="bind-agent-go-create"
					>
						Go to Agents
					</AppLink>
				}
			/>
		);
	} else {
		body = (
			<div className="space-y-4">
				{usable.length > 1 ? (
					<div className="space-y-1.5">
						<Label htmlFor="bind-agent-credential">Credential</Label>
						<Select
							id="bind-agent-credential"
							value={credential?.credential_id ?? ''}
							onChange={(e): void => setCredentialId(e.target.value)}
							data-testid="bind-agent-credential"
						>
							{usable.map((c) => {
								const hint = credentialSiblingHint(c, usable);
								return (
									<option key={c.credential_id} value={c.credential_id}>
										{hint ? `${c.name} · ${hint}` : c.name}
									</option>
								);
							})}
						</Select>
					</div>
				) : (
					<p className="text-muted-foreground text-sm">
						Using credential{' '}
						<strong className="text-foreground">{credential?.name}</strong>.
					</p>
				)}
				<fieldset className="min-w-0 space-y-1.5">
					<legend className="text-foreground mb-1.5 text-sm font-medium">Agents</legend>
					{!boundKnown &&
						(boundHere.error ? (
							<ErrorAlert
								message="Couldn’t check which agents already use this credential."
								onRetry={boundHere.retry}
								retrying={boundHere.isFetching}
							/>
						) : (
							<p
								className="text-muted-foreground text-xs"
								data-testid="bind-agent-checking"
							>
								Checking which agents already use this credential…
							</p>
						))}
					<ul
						className="border-border bg-muted/20 max-h-64 space-y-0.5 overflow-y-auto rounded-lg border p-1.5"
						data-testid="bind-agent-list"
					>
						{candidates.map((a) => {
							const bound = alreadyBound.has(a.id);
							return (
								<li
									key={a.id}
									className="hover:bg-muted/40 flex items-center justify-between gap-2 rounded-md px-2 py-1.5"
									data-testid="bind-agent-option"
								>
									<Checkbox
										size="sm"
										checked={bound || selected.has(a.id)}
										disabled={bound || !boundKnown || bind.isPending}
										onChange={(): void => toggle(a.id)}
									>
										<span className="text-foreground text-sm">{a.name}</span>
									</Checkbox>
									<span className="flex shrink-0 items-center gap-1.5">
										{bound && (
											<span className="text-muted-foreground text-[11px]">
												Already bound
											</span>
										)}
										<ActorStatusBadge status={toActorStatus(a.status)} />
									</span>
								</li>
							);
						})}
					</ul>
				</fieldset>
				{bind.error && <ErrorAlert message={bind.error} />}
			</div>
		);
	}

	const footer = (
		<>
			<Button variant="ghost" size="sm" onClick={onClose}>
				Cancel
			</Button>
			<Button
				size="sm"
				onClick={handleBind}
				disabled={picked.length === 0 || bind.isPending || !credential || !boundKnown}
				loading={bind.isPending}
				data-testid="bind-agent-confirm"
			>
				{picked.length > 1 ? `Bind to ${picked.length} agents` : 'Bind to agent'}
			</Button>
		</>
	);

	return (
		<Dialog
			open={open}
			onClose={onClose}
			title="Bind to an agent"
			subtitle={`Give an existing agent access to ${apiLabel} through one of its credentials.`}
			footer={noAgents || usable.length === 0 ? undefined : footer}
		>
			{body}
		</Dialog>
	);
}
