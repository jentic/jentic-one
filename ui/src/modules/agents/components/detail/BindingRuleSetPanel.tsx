import { useState } from 'react';
import { Layers, Unlink } from 'lucide-react';
import { Badge, Button, ErrorAlert, Skeleton } from '@/shared/ui';
import { ruleSummary, type PermissionRule as DisplayRule } from '@/shared/lib';
import {
	useDetachAgentBindingRuleSet,
	type BindingPermissionRule,
	type BindingRuleSetEntity,
} from '@/modules/agents/api';
import { toDisplayRules } from '@/modules/agents/components/detail/shared';
import { ConfirmDialog } from '@/modules/agents/components/confirm/ConfirmDialog';

/**
 * The permission rules of a direct binding that points at a shared rule set.
 *
 * While a set is attached the broker evaluates the SET's ordered rules and the
 * binding's inline rules are dormant: an inline save is stored but changes
 * nothing the agent can reach. So this panel stands in for the inline editor —
 * the set's rules read-only, the dormant inline count, and one way out: detach
 * the set, after which the inline rules apply and the editor returns.
 */
export interface BindingRuleSetPanelProps {
	agentId: string;
	credentialId: string;
	credentialLabel: string;
	/** The attached set; undefined while it loads or after a failed read. */
	ruleSet: BindingRuleSetEntity | undefined;
	isPending: boolean;
	isError: boolean;
	onRetry: () => void;
	/** The binding's dormant inline rules — what a detach makes effective.
	 * Undefined while unknown. */
	inlineRules: BindingPermissionRule[] | undefined;
}

/** One rule in the shared `ruleSummary` voice, without the trailing period. */
function oneLiner(rule: DisplayRule): string {
	return ruleSummary([rule]).replace(/\.$/, '');
}

function ruleCount(count: number): string {
	return count === 1 ? '1 rule' : `${count} rules`;
}

export function BindingRuleSetPanel({
	agentId,
	credentialId,
	credentialLabel,
	ruleSet,
	isPending,
	isError,
	onRetry,
	inlineRules,
}: BindingRuleSetPanelProps) {
	const [confirmOpen, setConfirmOpen] = useState(false);
	const detach = useDetachAgentBindingRuleSet(agentId, credentialId);
	const setRules = toDisplayRules(ruleSet?.rules);
	const dormant = inlineRules === undefined ? undefined : toDisplayRules(inlineRules);
	const setName = ruleSet?.name ?? 'the attached rule set';

	return (
		<div
			className="border-border bg-muted/20 space-y-4 rounded-lg border p-4 sm:p-5"
			data-testid="binding-rule-set-panel"
		>
			<div>
				<p className="text-foreground text-sm font-semibold">
					Permission rules for {credentialLabel}
				</p>
				<p className="text-muted-foreground mt-0.5 text-xs">
					This binding uses a shared rule set. Its rules are evaluated in order — first
					match wins, anything unmatched is denied — and are what the broker applies.
				</p>
			</div>

			{isPending ? (
				<div role="status" aria-live="polite" aria-busy="true">
					<span className="sr-only">Loading rule set…</span>
					<Skeleton className="h-24 rounded-lg" />
				</div>
			) : isError || !ruleSet ? (
				<ErrorAlert message="Failed to load the attached rule set." onRetry={onRetry} />
			) : (
				<div className="border-border/60 bg-card space-y-3 rounded-lg border p-3">
					<div className="flex flex-wrap items-center gap-2">
						<Layers
							className="text-muted-foreground h-4 w-4 shrink-0"
							aria-hidden="true"
						/>
						<span
							className="text-foreground text-sm font-medium"
							data-testid="rule-set-name"
						>
							{ruleSet.name}
						</span>
						{ruleSet.curated && <Badge data-testid="rule-set-curated">Curated</Badge>}
					</div>
					{ruleSet.description && (
						<p className="text-muted-foreground text-xs">{ruleSet.description}</p>
					)}
					{setRules.length === 0 ? (
						<p className="text-warning text-xs">
							This rule set has no rules — all calls are blocked.
						</p>
					) : (
						<ol className="space-y-1 text-xs" aria-label={`Rules in ${ruleSet.name}`}>
							{setRules.map((rule, i) => (
								<li key={i} className="text-foreground flex items-start gap-2">
									<span className="text-muted-foreground shrink-0 font-mono">
										#{i + 1}
									</span>
									<span>{oneLiner(rule)}</span>
								</li>
							))}
						</ol>
					)}
					<p className="text-muted-foreground text-xs">
						{ruleSet.bindingCount > 1
							? `Shared by ${ruleSet.bindingCount} bindings — a change to the set changes all of them.`
							: 'Used by this binding only.'}
						{ruleSet.curated &&
							' Curated by an org admin; only an org admin can edit it.'}
					</p>
				</div>
			)}

			<div className="flex flex-wrap items-center justify-between gap-2">
				<p className="text-muted-foreground text-xs" data-testid="dormant-inline-rules">
					{dormant === undefined
						? 'Inline rules are not edited while a rule set is attached.'
						: dormant.length === 0
							? 'This binding has no inline rules of its own.'
							: `${ruleCount(dormant.length)} of this binding's own ${dormant.length === 1 ? 'is' : 'are'} dormant while the set is attached.`}
				</p>
				<Button size="sm" variant="secondary" onClick={() => setConfirmOpen(true)}>
					<Unlink className="h-4 w-4" /> Detach rule set to edit inline rules
				</Button>
			</div>

			{/* A stateless confirm, so conditional mounting is the sanctioned lifecycle. */}
			{confirmOpen && (
				<ConfirmDialog
					open
					title="Detach rule set"
					body={
						<div className="space-y-2">
							<p>
								Detach <strong>{setName}</strong> from {credentialLabel}? The broker
								then applies this binding&apos;s own inline rules
								{dormant === undefined
									? '.'
									: dormant.length === 0
										? ' — it has none, so every call is blocked until you add one.'
										: ':'}
							</p>
							{dormant && dormant.length > 0 && (
								<ol className="space-y-1 text-xs">
									{dormant.map((rule, i) => (
										<li key={i}>
											#{i + 1} {oneLiner(rule)}
										</li>
									))}
								</ol>
							)}
						</div>
					}
					confirmLabel="Detach rule set"
					pending={detach.isPending}
					onConfirm={() =>
						detach.mutate(undefined, { onSuccess: () => setConfirmOpen(false) })
					}
					onClose={() => setConfirmOpen(false)}
				/>
			)}
		</div>
	);
}
