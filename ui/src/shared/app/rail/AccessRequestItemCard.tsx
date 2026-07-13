import { useEffect, useRef } from 'react';
import { motion } from 'framer-motion';
import { CheckCircle2, Info, Key, KeyRound, MessageSquare, X, Zap } from 'lucide-react';
import { Button } from '@/shared/ui/Button';
import { Textarea } from '@/shared/ui/Textarea';
import {
	itemTargetLabel,
	isSpecificResource,
	isScopeGrant,
	scopeLabel,
	rulesAreEnforceable,
	parseItemRules,
	type AccessRequestItem,
} from '@/shared/lib';
import { OperationsSummary } from '@/shared/app/rail/OperationsSummary';

/**
 * AccessRequestItemCard — one pending item in the dialog's "Awaiting Decision"
 * rail. Ported from jentic-webapp's `PendingRequestCard`, re-expressed on
 * jentic-one tokens.
 *
 * Accent palette (carries meaning, per the original webapp flow):
 *   accent-blue   → a SPECIFIC target (`resource_id` present) — "specific resource"/info
 *   accent-orange → the requested ACTION (the operation the agent wants) — deviation hue
 *   accent-yellow → a PLATFORM SCOPE grant (`scope.grant`) — coarse, high-trust
 *
 * Only a `credential.bind` item surfaces an enforceable allowlist: its
 * permission `rules` are written verbatim to the binding and enforced by the
 * broker (keyed per credential), so we render them read-only via
 * `OperationsSummary` (see `rulesAreEnforceable`). Rules attached to any other
 * item type — e.g. a `toolkit.bind` (agent↔toolkit), which has no credential to
 * key rules on — would NOT be enforced; rather than show a misleading allowlist
 * we render a non-enforceable notice. (The backend now rejects such rules at
 * file/amend time, so this guards legacy items only.)
 * A `scope.grant` item grants a coarse PLATFORM capability (its `resource_id`
 * IS the scope string) — it gets its own "Platform scope" treatment so it's
 * never mistaken for a narrow per-resource grant.
 *
 * Approve drafts the item `approved`. Deny flips the card into an INLINE deny
 * state — the reason field expands in place (rather than being buried in a
 * later step), so the operator writes the "no, because…" exactly where they
 * decide it. The reason is what reaches the agent on `:decide`.
 */
interface AccessRequestItemCardProps {
	item: AccessRequestItem;
	/** When set, the card is in its inline "deny" state showing the reason field. */
	denying: boolean;
	reason: string;
	onApprove: () => void;
	onStartDeny: () => void;
	onCancelDeny: () => void;
	onReasonChange: (reason: string) => void;
}

export function AccessRequestItemCard({
	item,
	denying,
	reason,
	onApprove,
	onStartDeny,
	onCancelDeny,
	onReasonChange,
}: AccessRequestItemCardProps) {
	const scopeGrant = isScopeGrant(item);
	const label = scopeGrant ? scopeLabel(item) : itemTargetLabel(item);
	const specific = isSpecificResource(item) && !scopeGrant;
	const rules = parseItemRules(item);
	const enforceable = rulesAreEnforceable(item);
	// Rules present on an item type that can't enforce them (e.g. a legacy
	// toolkit.bind): show a notice instead of an allowlist that won't apply.
	const hasUnenforceableRules = rules.length > 0 && !enforceable;
	const initials = label
		.replace(/[^a-zA-Z0-9]/g, '')
		.slice(0, 2)
		.toUpperCase();
	const assignedTo = item.to_id ? `${item.to_type ?? 'target'} ${item.to_id}` : null;
	const reasonRef = useRef<HTMLTextAreaElement>(null);

	// Focus the reason field the moment the card enters its deny state, so the
	// operator can type immediately without a second click.
	useEffect(() => {
		if (denying) reasonRef.current?.focus();
	}, [denying]);

	return (
		<motion.div
			layout
			initial={{ opacity: 0, scale: 0.96 }}
			animate={{ opacity: 1, scale: 1 }}
			exit={{ opacity: 0, scale: 0.92 }}
			transition={{ type: 'spring', stiffness: 380, damping: 30 }}
			className={`bg-card/60 relative flex w-full shrink-0 flex-col rounded-xl border transition-colors sm:w-[300px] ${
				denying ? 'border-danger/40' : 'border-border hover:border-border-hover'
			}`}
		>
			<div className="flex flex-1 flex-col p-3.5">
				<div className="flex items-start gap-3">
					<span
						className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg font-mono text-xs font-semibold ${
							scopeGrant
								? 'bg-accent-yellow/15 text-accent-yellow'
								: specific
									? 'bg-accent-blue/15 text-accent-blue'
									: 'bg-accent-orange/15 text-accent-orange'
						}`}
						aria-hidden="true"
					>
						{scopeGrant ? (
							<KeyRound className="h-4 w-4" />
						) : (
							initials ||
							(specific ? <Key className="h-4 w-4" /> : <Zap className="h-4 w-4" />)
						)}
					</span>
					<div className="min-w-0 flex-1">
						<h4
							className="text-foreground truncate text-sm font-semibold"
							title={label}
						>
							{label}
						</h4>
						<p className="text-muted-foreground truncate text-xs">
							{scopeGrant ? 'Platform scope' : item.resource_type}
						</p>
					</div>
					{!denying && (
						<button
							type="button"
							onClick={onStartDeny}
							title="Deny this item"
							aria-label={`Deny ${label}`}
							className="text-muted-foreground hover:bg-danger/10 hover:text-danger flex shrink-0 items-center justify-center rounded-full p-2.5 transition-colors sm:p-1"
						>
							<X className="h-4 w-4" aria-hidden="true" />
						</button>
					)}
				</div>

				<div className="mt-2.5 flex flex-wrap items-center gap-1.5 text-xs">
					{scopeGrant ? (
						<span className="bg-accent-yellow/10 text-accent-yellow inline-flex items-center gap-1 rounded px-1.5 py-0.5 font-medium">
							<KeyRound className="h-3 w-3" aria-hidden="true" />
							Platform scope
						</span>
					) : (
						<>
							<span
								className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 font-medium ${
									specific
										? 'bg-accent-blue/10 text-accent-blue'
										: 'bg-accent-orange/10 text-accent-orange'
								}`}
							>
								{specific ? (
									<Key className="h-3 w-3" aria-hidden="true" />
								) : (
									<Zap className="h-3 w-3" aria-hidden="true" />
								)}
								{item.action}
							</span>
							{specific && (
								<span className="bg-accent-blue/10 text-accent-blue rounded px-1.5 py-0.5 font-medium">
									Specific resource
								</span>
							)}
							{assignedTo && (
								<span className="bg-accent-orange/10 text-accent-orange rounded px-1.5 py-0.5 font-medium">
									&rarr; {assignedTo}
								</span>
							)}
						</>
					)}
				</div>

				{!scopeGrant && enforceable && (
					<OperationsSummary rules={rules} targetLabel={label} />
				)}

				{hasUnenforceableRules && (
					<div className="text-muted-foreground border-border bg-card/40 mt-2.5 flex items-start gap-1.5 rounded-lg border px-2.5 py-2 text-xs">
						<Info className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
						<span>
							Attached rules will not be enforced on a {item.resource_type} binding —
							attach rules to a credential binding to restrict operations.
						</span>
					</div>
				)}

				{/* Action block pinned to the bottom (`mt-auto`) so the Approve
				    button aligns across cards of differing content height — e.g. a
				    bare scope grant next to a credential.bind with a long ops list.
				    `pt-3` keeps a constant gap above the action even when `mt-auto`
				    collapses (the tallest card in the row). */}
				<div className="mt-auto pt-3">
					{denying ? (
						<motion.div
							initial={{ opacity: 0, height: 0 }}
							animate={{ opacity: 1, height: 'auto' }}
							className="overflow-hidden"
						>
							<label
								htmlFor={`ar-deny-${item.id}`}
								className="text-muted-foreground mb-1 flex items-center gap-1 text-[10px] font-medium tracking-wide uppercase"
							>
								<MessageSquare className="h-2.5 w-2.5" aria-hidden="true" />
								Why deny? (sent to the agent)
							</label>
							<Textarea
								ref={reasonRef}
								id={`ar-deny-${item.id}`}
								rows={2}
								resizable="none"
								value={reason}
								onChange={(e) => onReasonChange(e.target.value)}
								placeholder="e.g. scope too broad — request a single repo"
								className="text-xs"
							/>
							<div className="mt-2 flex gap-1.5">
								<Button
									variant="secondary"
									size="sm"
									onClick={onCancelDeny}
									className="flex-1 text-xs"
								>
									Cancel
								</Button>
								<Button
									variant="danger"
									size="sm"
									onClick={onStartDeny}
									disabled={reason.trim().length === 0}
									className="flex-1 text-xs font-semibold"
								>
									<X className="h-3.5 w-3.5" aria-hidden="true" />
									Confirm deny
								</Button>
							</div>
						</motion.div>
					) : (
						<button
							type="button"
							onClick={onApprove}
							className="bg-accent-green/10 text-accent-green hover:bg-accent-green/20 inline-flex w-full items-center justify-center gap-1.5 rounded-lg px-3 py-2 text-sm font-semibold transition-colors"
						>
							<CheckCircle2 className="h-4 w-4" aria-hidden="true" />
							Approve
						</button>
					)}
				</div>
			</div>
		</motion.div>
	);
}
