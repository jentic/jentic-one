/**
 * BindAgentDialog — "Bind to an agent", in place on the API hub's "Who can use
 * it" card: pick one of this API's credentials (when there are several), tick
 * active agents, say what they may call, confirm.
 *
 * A binding is default-deny — with no rules every call is refused — so the
 * dialog asks for the rules up front ("What can this agent call?"): Allow all
 * operations, Read-only (GET only), or Custom rules (the shared rules editor).
 * Nothing is preselected and Bind stays disabled until a choice is made (or,
 * for custom, at least one rule exists). The same rules go to every ticked
 * agent: each is bound (`POST /agents/{id}/credentials`), then its binding's
 * rules are replaced (`PUT /credentials/{cid}/agents/{aid}/permissions`). If a
 * rules save fails the binding stays — that agent reads Blocked — and the
 * dialog stays open on a Retry for just those rules.
 *
 * An API that needs no credential passes `createOnBind`: the dialog then binds
 * through a new no-auth credential that is created only when Bind is
 * confirmed, so cancelling leaves nothing behind.
 *
 * Offered only to a viewer with `agents:write` (the bind endpoint's
 * permission). The credential list is narrowed to the ones the viewer may bind
 * — the server lets a non-admin bind only credentials they created (else 404).
 * Only ACTIVE agents are offered (a pending, disabled, archived or rejected
 * agent serves nothing). Agents already bound to the chosen credential are
 * shown, disabled — read in full (every page, `useAllCredentialAgents`), so an
 * agent past the first page can't pass for unbound and be bound twice. Until
 * that read is whole, nothing can be ticked or bound.
 *
 * State lifecycle (dialog-state rule): the draft (credential, ticks, rules)
 * persists across dismissals and resets on a successful bind; transient errors
 * clear on reopen.
 */
import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { Bot } from 'lucide-react';
import {
	AppLink,
	Button,
	Checkbox,
	Dialog,
	EmptyState,
	ErrorAlert,
	Label,
	RadioCardGroup,
	Select,
	Skeleton,
	allowAllRule,
	cleanPermissionRule,
	isEmptyAllowRule,
	toast,
	type PermissionRuleInput,
} from '@/shared/ui';
import { ROUTES } from '@/shared/app/routes';
import { useOptionalCurrentUser } from '@/shared/auth';
import { credentialsBindableBy } from '@/shared/credentials/lib/bindAuthority';
import { credentialSiblingHint } from '@/shared/credentials/lib/credentialIdentity';
import { useAllCredentialAgents, type Credential } from '@/shared/credentials/api';
import {
	useAgentsForPicker,
	useApplyBindingRules,
	useBindCredentialToAgents,
	useVendorOperations,
} from '@/shared/credentials/api/vendors-hooks';
import type { PermissionRule } from '@/shared/credentials/api/vendors-types';
import { RuleListEditor } from '@/shared/credentials/components/RuleListEditor';

/** The rules choice; `null` until the user picks one. */
type RulesPreset = 'all' | 'read' | 'custom';

const PRESET_OPTIONS: { value: RulesPreset; label: string; description: string }[] = [
	{
		value: 'all',
		label: 'Allow all operations',
		description: 'Every operation of this API, any method.',
	},
	{
		value: 'read',
		label: 'Read-only (GET only)',
		description: 'GET requests only — nothing that changes data.',
	},
	{
		value: 'custom',
		label: 'Custom rules',
		description: 'Write your own allow / deny rules, evaluated in order.',
	},
];

/** The read-only preset: any path, GET only. */
function readOnlyRule(): PermissionRuleInput {
	return {
		effect: 'allow' as PermissionRuleInput['effect'],
		methods: ['GET'],
		path: null,
		operations: null,
	};
}

/** A rules-editor rule in the wire shape (same fields; enum types differ). */
function toWireRule(rule: PermissionRule): PermissionRuleInput {
	return cleanPermissionRule(rule as unknown as PermissionRuleInput);
}

export interface BindAgentDialogProps {
	open: boolean;
	onClose: () => void;
	/** Every credential whose scope covers this API. */
	credentials: Credential[];
	/** Shown in the subtitle, e.g. "NewsAPI". */
	apiLabel: string;
	/**
	 * The API (with a concrete version) whose operations seed the custom rules
	 * editor's path suggestions. Omitted → the editor works without them.
	 */
	apiReference?: { vendor: string; name: string; version: string } | null;
	/**
	 * Preselect this credential. Seeds the pick when it changes (a caller that
	 * just created a credential opens the dialog on it); the user can still
	 * switch.
	 */
	initialCredentialId?: string | null;
	/**
	 * No usable credential yet, but one can be made: shown as the credential
	 * to use, and created only when Bind is confirmed (`create` resolves to it).
	 */
	createOnBind?: { name: string; create: () => Promise<Credential> } | null;
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
	apiReference,
	initialCredentialId,
	createOnBind,
}: BindAgentDialogProps) {
	const viewer = useOptionalCurrentUser();
	// A credential this dialog created on Bind, held until the caller's list
	// carries it — so a retry never creates a second one.
	const [created, setCreated] = useState<Credential | null>(null);
	const known = useMemo(
		() =>
			created && !credentials.some((c) => c.credential_id === created.credential_id)
				? [...credentials, created]
				: credentials,
		[credentials, created],
	);
	const usable = useMemo(() => credentialsBindableBy(known, viewer), [known, viewer]);
	const agents = useAgentsForPicker();
	const bind = useBindCredentialToAgents();
	const applyRules = useApplyBindingRules();
	const rulesId = useId();

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
	const [preset, setPreset] = useState<RulesPreset | null>(null);
	const [customRules, setCustomRules] = useState<PermissionRule[]>([]);
	// A bind that landed but whose rules didn't, for some agents: kept so the
	// Retry saves exactly those rules on exactly those bindings.
	const [rulesFailure, setRulesFailure] = useState<{
		credentialId: string;
		agents: { id: string; name: string }[];
		rules: PermissionRuleInput[];
	} | null>(null);
	const [createError, setCreateError] = useState<Error | null>(null);
	const [creating, setCreating] = useState(false);

	// Transient: clear the last errors on every (re)open. (`reset` is stable.)
	const resetBind = bind.reset;
	const resetApply = applyRules.reset;
	useEffect(() => {
		if (open) {
			resetBind();
			resetApply();
			setCreateError(null);
		}
	}, [open, resetBind, resetApply]);

	// A stale / unusable pick falls back to the first usable credential.
	const credential = usable.find((c) => c.credential_id === credentialId) ?? usable[0] ?? null;
	// Bind through a credential made on confirm — only while none exists yet.
	const pendingCreate = credential == null ? (createOnBind ?? null) : null;
	// Every page: a partial list would offer already-bound agents as unbound.
	const boundHere = useAllCredentialAgents(credential?.credential_id, {
		enabled: open && credential != null,
	});
	// A credential that doesn't exist yet has no agents bound to it.
	const boundKnown = pendingCreate != null || boundHere.complete;
	const alreadyBound = useMemo(
		() => new Set(pendingCreate ? [] : boundHere.items.map((a) => a.agent_id)),
		[boundHere.items, pendingCreate],
	);

	const allAgents = useMemo(() => agents.data?.data ?? [], [agents.data]);
	// Only an active agent serves traffic — the others can't use a grant.
	const candidates = useMemo(() => allAgents.filter((a) => a.status === 'active'), [allAgents]);
	// Ticks only count for agents still bindable to the chosen credential.
	const picked = boundKnown
		? candidates.filter((a) => selected.has(a.id) && !alreadyBound.has(a.id))
		: [];
	const everyAgentBound =
		boundKnown && candidates.length > 0 && candidates.every((a) => alreadyBound.has(a.id));

	// The custom editor's path suggestions: this API's real operations.
	const opsQuery = useVendorOperations(apiReference ?? undefined, {
		enabled: open && preset === 'custom' && !!apiReference,
	});
	const pathSuggestions = useMemo<readonly string[]>(() => {
		const rows = opsQuery.data?.data;
		if (!rows) return [];
		return Array.from(new Set(rows.map((op) => op.path))).sort();
	}, [opsQuery.data]);

	// The rules every picked agent gets, or null while the choice is incomplete.
	const rules = useMemo<PermissionRuleInput[] | null>(() => {
		if (preset === 'all') return [cleanPermissionRule(allowAllRule())];
		if (preset === 'read') return [cleanPermissionRule(readOnlyRule())];
		if (preset === 'custom') {
			const wire = customRules.map(toWireRule);
			return wire.length > 0 && !wire.some(isEmptyAllowRule) ? wire : null;
		}
		return null;
	}, [preset, customRules]);

	const busy = bind.isPending || applyRules.isPending || creating;

	const toggle = (id: string): void =>
		setSelected((prev) => {
			const next = new Set(prev);
			if (next.has(id)) next.delete(id);
			else next.add(id);
			return next;
		});

	const finish = (credentialName: string, names: string[]): void => {
		toast({
			title: 'Credential bound',
			description: `Bound “${credentialName}” to ${agentList(names)}, with access rules.`,
			variant: 'success',
		});
		// Reset the draft only on a successful commit.
		setSelected(new Set());
		setPreset(null);
		setCustomRules([]);
		setRulesFailure(null);
		onClose();
	};

	const handleBind = async (): Promise<void> => {
		if (picked.length === 0 || rules == null || busy) return;
		setCreateError(null);
		let target = credential;
		if (!target && pendingCreate) {
			setCreating(true);
			try {
				target = await pendingCreate.create();
			} catch (error) {
				setCreateError(error instanceof Error ? error : new Error(String(error)));
				return;
			} finally {
				setCreating(false);
			}
			setCreated(target);
			setCredentialId(target.credential_id);
		}
		if (!target) return;
		const bound = target;
		const agentsToBind = picked.map((a) => ({ id: a.id, name: a.name }));
		bind.mutate(
			{ credentialId: bound.credential_id, agentIds: agentsToBind.map((a) => a.id), rules },
			{
				onSuccess: ({ rulesFailed }) => {
					if (rulesFailed.length === 0) {
						finish(
							bound.name,
							agentsToBind.map((a) => a.name),
						);
						return;
					}
					setSelected(new Set());
					setRulesFailure({
						credentialId: bound.credential_id,
						agents: agentsToBind.filter((a) => rulesFailed.includes(a.id)),
						rules,
					});
				},
			},
		);
	};

	const retryRules = (): void => {
		if (!rulesFailure) return;
		const failure = rulesFailure;
		applyRules.mutate(
			{
				credentialId: failure.credentialId,
				agentIds: failure.agents.map((a) => a.id),
				rules: failure.rules,
			},
			{
				onSuccess: ({ rulesFailed }) => {
					const left = failure.agents.filter((a) => rulesFailed.includes(a.id));
					if (left.length > 0) {
						setRulesFailure({ ...failure, agents: left });
						return;
					}
					const name =
						usable.find((c) => c.credential_id === failure.credentialId)?.name ??
						'the credential';
					finish(
						name,
						failure.agents.map((a) => a.name),
					);
				},
			},
		);
	};

	const noActiveAgents = !agents.isPending && !agents.error && candidates.length === 0;
	const noCredential = usable.length === 0 && pendingCreate == null;

	let body: React.ReactNode;
	if (noCredential) {
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
	} else if (noActiveAgents) {
		body = (
			<EmptyState
				icon={<Bot className="h-6 w-6" aria-hidden="true" />}
				title={allAgents.length === 0 ? 'No agents yet' : 'No active agents'}
				description={
					allAgents.length === 0
						? "Create or register an agent first, then bind this API's credential to it."
						: 'Only active agents can be given access. Approve or re-enable an agent first.'
				}
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
				) : pendingCreate ? (
					<p
						className="text-muted-foreground text-sm"
						data-testid="bind-agent-create-note"
					>
						Uses a new no-auth credential,{' '}
						<strong className="text-foreground">{pendingCreate.name}</strong> — created
						when you bind (there&apos;s no secret to set up).
					</p>
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
					{everyAgentBound && !rulesFailure ? (
						<p
							className="text-muted-foreground text-sm"
							data-testid="bind-agent-all-bound"
						>
							Every active agent already uses this credential.
						</p>
					) : (
						<ul
							className="bg-field max-h-64 space-y-0.5 overflow-y-auto rounded-lg p-1.5"
							data-testid="bind-agent-list"
						>
							{candidates.map((a) => {
								const bound = alreadyBound.has(a.id);
								return (
									<li
										key={a.id}
										className="hover:bg-tint-2 flex items-center justify-between gap-2 rounded-md px-2 py-1.5"
										data-testid="bind-agent-option"
									>
										<Checkbox
											size="sm"
											checked={bound || selected.has(a.id)}
											disabled={bound || !boundKnown || busy}
											onChange={(): void => toggle(a.id)}
										>
											<span className="text-foreground-name text-sm">
												{a.name}
											</span>
										</Checkbox>
										{bound && (
											<span className="text-muted-foreground shrink-0 text-[11px]">
												Already bound
											</span>
										)}
									</li>
								);
							})}
						</ul>
					)}
				</fieldset>
				{!everyAgentBound && (
					<div className="space-y-2" data-testid="bind-agent-rules">
						<p id={rulesId} className="text-foreground text-sm font-medium">
							What can this agent call?
						</p>
						<RadioCardGroup
							options={PRESET_OPTIONS}
							value={preset}
							onChange={setPreset}
							ariaLabelledBy={rulesId}
							disabled={busy}
							data-testid="bind-agent-rules-preset"
						/>
						{preset === 'custom' && (
							<RuleListEditor
								rules={customRules}
								onChange={setCustomRules}
								pathSuggestions={pathSuggestions}
								opTemplates={pathSuggestions}
								opsLoaded={pathSuggestions.length > 0}
							/>
						)}
					</div>
				)}
				{createError && (
					<ErrorAlert
						title="Couldn't create the no-auth credential"
						message={createError}
					/>
				)}
				{bind.error && <ErrorAlert message={bind.error} />}
				{rulesFailure && (
					<div data-testid="bind-agent-rules-failed">
						<ErrorAlert
							title="Bound, but the access rules weren't saved"
							message={`${agentList(rulesFailure.agents.map((a) => a.name))} ${
								rulesFailure.agents.length === 1 ? 'is' : 'are'
							} bound but blocked — every call is denied until the rules are saved.`}
							onRetry={retryRules}
							retrying={applyRules.isPending}
						/>
					</div>
				)}
			</div>
		);
	}

	const footer = (
		<>
			<Button variant="ghost" size="sm" onClick={onClose}>
				{rulesFailure ? 'Close' : 'Cancel'}
			</Button>
			<Button
				size="sm"
				onClick={(): void => void handleBind()}
				disabled={
					picked.length === 0 ||
					rules == null ||
					busy ||
					(!credential && !pendingCreate) ||
					!boundKnown
				}
				loading={bind.isPending || creating}
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
			footer={noActiveAgents || noCredential ? undefined : footer}
		>
			{body}
		</Dialog>
	);
}
