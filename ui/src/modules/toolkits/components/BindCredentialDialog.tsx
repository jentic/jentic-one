import { useState } from 'react';
import { ArrowLeft, KeyRound, ListChecks, ShieldBan, ShieldCheck } from 'lucide-react';
import {
	AppLink,
	Button,
	Dialog,
	PermissionRuleEditor,
	cleanPermissionRule,
	isEmptyAllowRule,
	type PermissionRuleInput,
} from '@/shared/ui';
import { ROUTES } from '@/shared/app/routes';
import { cn } from '@/shared/lib/utils';
import { useBindCredential } from '@/modules/toolkits/api';
import type { BindableCredential } from '@/modules/toolkits/api/types';
import { CREDENTIAL_TYPE_LABELS } from '@/modules/toolkits/api/types';
import { CredentialPicker } from '@/modules/toolkits/components/CredentialPicker';

/**
 * Two-step "Bind credential" dialog — pick a credential, then decide what it
 * may do.
 *
 * The old dialog bound on click with ZERO rules, silently landing the binding
 * in the broker's default-deny state ("all ops blocked") that the user then had
 * to discover via a warning and fix in a second surface. The bind endpoint has
 * always accepted `allow_all` / `permissions` inline; this dialog finally uses
 * them, so a binding leaves the dialog in the exact access state the operator
 * chose — the same "decide the grant at bind time" shape the access-request
 * fulfilment wizard uses.
 *
 * Lifecycle: wizard — draft (selection, mode, rules) persists across casual
 * dismissals and resets only on a successful bind (dialog-state rule).
 *
 * When the bind lands on a toolkit that nothing can use yet (no linked agent,
 * no API key — see `agentless`), the dialog holds open on a "link an agent?"
 * prompt instead of closing into silence: the proactive half of the
 * manual-setup dead-end fix (issue #826), complementing the detail page's
 * reactive no-linked-agents banner.
 */

type AccessMode = 'allow_all' | 'custom' | 'blocked';

interface ModeOption {
	value: AccessMode;
	label: string;
	description: string;
	Icon: typeof ShieldCheck;
	/** Chip styling matching the platform's effect colours (allow/deny). */
	chip: string;
}

const MODE_OPTIONS: ModeOption[] = [
	{
		value: 'allow_all',
		label: 'Allow all operations',
		description:
			'One allow rule matching every request — the broadest grant. You can narrow it later from this tab.',
		Icon: ShieldCheck,
		chip: 'bg-accent-green/10 text-accent-green',
	},
	{
		value: 'custom',
		label: 'Custom rules',
		description: 'Author allow/deny rules now. First match wins; anything unmatched is denied.',
		Icon: ListChecks,
		chip: 'bg-accent-blue/10 text-accent-blue',
	},
	{
		value: 'blocked',
		label: 'Start blocked',
		description:
			'Bind without rules — the broker denies every call until you add rules. Useful to stage a binding.',
		Icon: ShieldBan,
		chip: 'bg-muted text-muted-foreground',
	},
];

export interface BindCredentialDialogProps {
	toolkitId: string;
	open: boolean;
	onClose: () => void;
	/** Credential ids already bound to this toolkit — hidden from the picker. */
	boundIds: Set<string>;
	/**
	 * True when the freshly-bound credential would serve nothing: no agent is
	 * linked to the toolkit and no API key exists. Enables the post-bind
	 * "link an agent?" prompt — the proactive half of the manual-setup
	 * dead-end fix (issue #826; the detail-page banner is the reactive half).
	 */
	agentless?: boolean;
	/**
	 * Where "Link an agent" leads (open the picker, or jump to the tab that
	 * hosts it). The post-bind prompt is only offered when this is provided.
	 */
	onLinkAgent?: () => void;
}

export function BindCredentialDialog({
	toolkitId,
	open,
	onClose,
	boundIds,
	agentless = false,
	onLinkAgent,
}: BindCredentialDialogProps) {
	const bindCredential = useBindCredential(toolkitId);

	const [selected, setSelected] = useState<BindableCredential | null>(null);
	const [mode, setMode] = useState<AccessMode>('allow_all');
	const [rules, setRules] = useState<PermissionRuleInput[]>([]);
	// Post-success state: the bind landed but nothing can use it yet — hold the
	// dialog open on a "link an agent?" prompt instead of closing into silence.
	const [justBound, setJustBound] = useState(false);

	const step: 'pick' | 'access' | 'linkPrompt' = justBound
		? 'linkPrompt'
		: selected
			? 'access'
			: 'pick';

	// Strip empty conditions the same way the permissions editor's save does, so
	// the bind body never carries `methods: []` / `path: ""` noise (and prefix/
	// exact match modes survive — one shared cleaner for every save/bind path).
	const cleanRules = rules.map(cleanPermissionRule);
	const customInvalid =
		mode === 'custom' && (cleanRules.length === 0 || cleanRules.some(isEmptyAllowRule));

	const reset = () => {
		setSelected(null);
		setMode('allow_all');
		setRules([]);
		bindCredential.reset();
	};

	const close = () => {
		setJustBound(false);
		onClose();
	};

	const submit = () => {
		if (!selected || customInvalid) return;
		bindCredential.mutate(
			{
				credential_id: selected.credential_id,
				...(mode === 'allow_all' ? { allow_all: true } : {}),
				...(mode === 'custom' ? { permissions: cleanRules } : {}),
			},
			{
				onSuccess: () => {
					reset();
					if (agentless && onLinkAgent) {
						setJustBound(true);
					} else {
						onClose();
					}
				},
			},
		);
	};

	return (
		<Dialog
			open={open}
			onClose={close}
			title="Bind credential"
			subtitle={
				step === 'pick'
					? 'Step 1 of 2 · pick a credential'
					: step === 'access'
						? 'Step 2 of 2 · decide what it may do'
						: 'Bound · one step left to put it to use'
			}
			size="lg"
			footer={
				step === 'pick' ? (
					<Button variant="secondary" onClick={close}>
						Cancel
					</Button>
				) : step === 'linkPrompt' ? (
					<>
						<Button variant="secondary" onClick={close}>
							Not now
						</Button>
						<Button
							onClick={() => {
								setJustBound(false);
								onLinkAgent?.();
								onClose();
							}}
						>
							Link an agent
						</Button>
					</>
				) : (
					<>
						<Button variant="secondary" onClick={() => setSelected(null)}>
							<ArrowLeft className="h-4 w-4" /> Back
						</Button>
						<Button
							onClick={submit}
							loading={bindCredential.isPending}
							disabled={customInvalid}
						>
							{bindCredential.isPending ? 'Binding…' : 'Bind credential'}
						</Button>
					</>
				)
			}
		>
			{step === 'pick' ? (
				<div className="space-y-3">
					<p className="text-muted-foreground text-sm">
						Pick a credential to bind to this toolkit. Manage credentials on the{' '}
						<AppLink href={ROUTES.credentials} className="text-primary font-medium">
							Credentials
						</AppLink>{' '}
						page.
					</p>
					<CredentialPicker boundIds={boundIds} onSelect={setSelected} enabled={open} />
				</div>
			) : step === 'linkPrompt' ? (
				<div className="flex items-start gap-3" data-testid="bind-link-agent-prompt">
					<div className="bg-accent-green/10 text-accent-green flex h-9 w-9 shrink-0 items-center justify-center rounded-lg">
						<ShieldCheck className="h-5 w-5" aria-hidden="true" />
					</div>
					<div className="min-w-0 flex-1">
						<p className="text-foreground text-sm font-medium">Credential bound.</p>
						<p className="text-muted-foreground mt-1 text-sm">
							But no agent is linked to this toolkit yet, so nothing can reach the
							credential — calls will only start working once an agent is linked (or
							an API key is issued).
						</p>
					</div>
				</div>
			) : (
				selected && (
					<div className="space-y-4">
						{/* Recap of step 1's choice, with the way back. Blue is the
						    credential accent everywhere (yellow is API keys). */}
						<div className="bg-muted/30 border-border flex items-center gap-3 rounded-lg border p-3">
							<div className="bg-accent-blue/10 text-accent-blue flex h-8 w-8 shrink-0 items-center justify-center rounded-lg">
								<KeyRound className="h-4 w-4" />
							</div>
							<div className="min-w-0 flex-1">
								<span className="text-foreground block truncate text-sm font-medium">
									{selected.name}
								</span>
								<p className="text-muted-foreground truncate font-mono text-xs">
									{selected.vendor ?? selected.provider ?? selected.credential_id}
									{' · '}
									{CREDENTIAL_TYPE_LABELS[selected.type] ?? selected.type}
								</p>
							</div>
							<Button variant="ghost" size="sm" onClick={() => setSelected(null)}>
								Change
							</Button>
						</div>

						<div role="radiogroup" aria-label="Access level" className="space-y-2">
							{MODE_OPTIONS.map(({ value, label, description, Icon, chip }, i) => {
								const active = mode === value;
								return (
									<button
										key={value}
										type="button"
										role="radio"
										aria-checked={active}
										// ARIA radio pattern: one tab stop for the group,
										// arrows move the selection.
										tabIndex={active ? 0 : -1}
										onClick={() => setMode(value)}
										onKeyDown={(e) => {
											const delta =
												e.key === 'ArrowDown' || e.key === 'ArrowRight'
													? 1
													: e.key === 'ArrowUp' || e.key === 'ArrowLeft'
														? -1
														: 0;
											if (!delta) return;
											e.preventDefault();
											const next =
												MODE_OPTIONS[
													(i + delta + MODE_OPTIONS.length) %
														MODE_OPTIONS.length
												];
											setMode(next.value);
											(
												e.currentTarget.parentElement?.querySelectorAll(
													'[role="radio"]',
												)?.[
													(i + delta + MODE_OPTIONS.length) %
														MODE_OPTIONS.length
												] as HTMLElement | undefined
											)?.focus();
										}}
										className={cn(
											'flex w-full items-start gap-3 rounded-lg border p-3 text-left transition-colors',
											active
												? 'border-primary/60 bg-primary/5'
												: 'border-border hover:border-border hover:bg-muted/40',
										)}
									>
										<span
											aria-hidden="true"
											className={cn(
												'flex h-8 w-8 shrink-0 items-center justify-center rounded-lg',
												chip,
											)}
										>
											<Icon className="h-4 w-4" />
										</span>
										<span className="min-w-0 flex-1">
											<span className="text-foreground block text-sm font-medium">
												{label}
											</span>
											<span className="text-muted-foreground mt-0.5 block text-xs leading-snug">
												{description}
											</span>
										</span>
										<span
											aria-hidden="true"
											className={cn(
												'mt-1 flex h-4 w-4 shrink-0 items-center justify-center rounded-full border-2 transition-colors',
												active
													? 'border-primary'
													: 'border-muted-foreground/30',
											)}
										>
											{active && (
												<span className="bg-primary h-2 w-2 rounded-full" />
											)}
										</span>
									</button>
								);
							})}
						</div>

						{mode === 'custom' && (
							<div className="border-border bg-muted/20 rounded-lg border p-3">
								<PermissionRuleEditor rules={rules} onChange={setRules} />
							</div>
						)}
					</div>
				)
			)}
		</Dialog>
	);
}
