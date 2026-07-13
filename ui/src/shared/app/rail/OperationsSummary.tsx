import { useState } from 'react';
import {
	AlertTriangle,
	ListChecks,
	Maximize2,
	ShieldAlert,
	ShieldBan,
	ShieldCheck,
} from 'lucide-react';
import { isUnrestrictedAllow, ruleSummary, type PermissionRule } from '@/shared/lib';
import { OperationsDialog } from '@/shared/app/rail/OperationsDialog';

/**
 * OperationsSummary — read-only, BOUNDED preview of the allow/block operations a
 * `credential.bind` item grants. On approval these rules are written verbatim
 * to the binding (broker-enforced), so the reviewer needs to SEE what they're
 * granting before they approve — otherwise they approve blind.
 *
 * Bounded by design: this never tries to render a whole grant inline. Each rule
 * shows its effect + method/path and at most a handful of example operations;
 * the full set (which can be 100+ operations) lives in the dedicated
 * {@link OperationsDialog}, reached via "View all N operations". That keeps a
 * card's height constant regardless of how large the grant is — the wall of
 * chips that a big grant used to produce can't happen here.
 *
 * Read-only by design: the `:decide` verb only accepts approve/deny + reason,
 * so narrowing the rule set is a separate `:amend` concern, not something this
 * card can do. We surface, we don't edit.
 *
 * Accessibility: colour is never the only signal — each rule leads with an
 * effect WORD ("Allow"/"Block"/"Needs approval") and a distinct icon, and the
 * whole block carries an `aria-label` summary (`ruleSummary`) so a screen-reader
 * user hears "Blocks DELETE; Allows GET on 3 operations" without parsing
 * individual chips. Paths and operationIds wrap rather than truncate — on touch
 * there is no hover `title` to recover a clipped value.
 */

const EFFECT_STYLES: Record<
	PermissionRule['effect'],
	{ label: string; chip: string; Icon: typeof ShieldCheck }
> = {
	allow: {
		label: 'Allow',
		chip: 'bg-accent-green/10 text-accent-green',
		Icon: ShieldCheck,
	},
	deny: {
		label: 'Block',
		chip: 'bg-danger/10 text-danger',
		Icon: ShieldBan,
	},
	'require-approval': {
		label: 'Needs approval',
		chip: 'bg-accent-orange/10 text-accent-orange',
		Icon: ShieldAlert,
	},
};

/** How many operationIds to show inline before deferring the rest to the dialog. */
const OPS_PREVIEW = 4;

function RuleRow({ rule }: { rule: PermissionRule }) {
	// An unrestricted allow grants blanket access (matches every request). Render
	// it in danger styling with an explicit "unrestricted" word and a warning
	// icon so a reviewer can't approve it blind — it overrides the bland green
	// "Allow" treatment a constrained allow gets.
	const unrestricted = isUnrestrictedAllow(rule);
	const { label, chip, Icon } = unrestricted
		? { label: 'Allow', chip: 'bg-danger/10 text-danger', Icon: AlertTriangle }
		: EFFECT_STYLES[rule.effect];
	const ops = rule.operations ?? [];
	const shownOps = ops.slice(0, OPS_PREVIEW);
	const overflow = ops.length - shownOps.length;

	return (
		<li className="flex flex-col gap-1">
			<div className="flex flex-wrap items-center gap-1.5 text-xs">
				<span
					className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 font-semibold ${chip}`}
				>
					<Icon className="h-3 w-3" aria-hidden="true" />
					{label}
				</span>
				{unrestricted ? (
					<span className="text-danger font-semibold">any request (unrestricted)</span>
				) : rule.methods?.length ? (
					<span className="text-foreground font-mono">{rule.methods.join(' ')}</span>
				) : !ops.length && !rule.path ? (
					<span className="text-muted-foreground">any request</span>
				) : null}
				{rule.path ? (
					<span className="text-muted-foreground font-mono break-all">{rule.path}</span>
				) : null}
			</div>
			{ops.length > 0 && (
				<div className="flex flex-wrap items-center gap-1">
					{shownOps.map((op) => (
						<span
							key={op}
							className="bg-muted text-muted-foreground rounded px-1.5 py-0.5 font-mono text-[10px] break-all"
						>
							{op}
						</span>
					))}
					{overflow > 0 && (
						<span className="text-muted-foreground/70 text-[10px]">
							+{overflow} more
						</span>
					)}
				</div>
			)}
		</li>
	);
}

export function OperationsSummary({
	rules,
	targetLabel,
}: {
	rules: PermissionRule[];
	/** Optional label of the owning item, forwarded to the full-view dialog. */
	targetLabel?: string;
}) {
	const [dialogOpen, setDialogOpen] = useState(false);
	if (rules.length === 0) return null;
	// Total operations across all rules — surfaced in the eyebrow so the reviewer
	// sees the magnitude of a large grant ("Operations granted · 62") even before
	// opening the full view.
	const totalOps = rules.reduce((n, r) => n + (r.operations?.length ?? 0), 0);
	// Anything beyond a single rule's inline preview is worth a full view; gate
	// the affordance so a tiny 2-operation grant doesn't get a pointless button.
	const hasMore = rules.length > 1 || totalOps > OPS_PREVIEW;
	return (
		<section
			className="border-border/60 bg-muted/30 mt-2.5 rounded-lg border p-2"
			aria-label={ruleSummary(rules)}
		>
			<div className="mb-1.5 flex items-center justify-between gap-2">
				<p className="text-muted-foreground flex items-center gap-1 text-[10px] font-medium tracking-wide uppercase">
					<ListChecks className="h-2.5 w-2.5" aria-hidden="true" />
					Operations granted
				</p>
				{hasMore ? (
					<button
						type="button"
						onClick={() => setDialogOpen(true)}
						className="text-accent-blue hover:text-accent-blue/80 inline-flex shrink-0 items-center gap-1 rounded p-1 text-[10px] font-medium sm:p-0.5"
					>
						View all {totalOps > 0 ? totalOps : rules.length}
						<Maximize2 className="h-2.5 w-2.5" aria-hidden="true" />
					</button>
				) : (
					totalOps > 0 && (
						<span className="text-muted-foreground/70 shrink-0 text-[10px] tabular-nums">
							{totalOps}
						</span>
					)
				)}
			</div>
			<ul className="flex flex-col gap-1.5">
				{rules.map((rule) => (
					<RuleRow
						key={`${rule.effect}:${rule.path ?? ''}:${(rule.methods ?? []).join(',')}:${(rule.operations ?? []).join(',')}`}
						rule={rule}
					/>
				))}
			</ul>
			{/* Mount the dialog only while open: it keeps a card's idle DOM tiny
			    (a 100-operation grant doesn't render 100 hidden nodes per card)
			    and avoids its "Operations granted" title colliding with this
			    section's eyebrow in the accessibility tree. */}
			{dialogOpen && (
				<OperationsDialog
					open
					onClose={() => setDialogOpen(false)}
					rules={rules}
					targetLabel={targetLabel}
				/>
			)}
		</section>
	);
}
