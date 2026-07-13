import { useMemo, useState } from 'react';
import { Filter, ListChecks, ShieldAlert, ShieldBan, ShieldCheck } from 'lucide-react';
import { Dialog } from '@/shared/ui/Dialog';
import { SearchInput } from '@/shared/ui/SearchInput';
import { ruleSummary, type PermissionRule } from '@/shared/lib';

/**
 * OperationsDialog — the full, scannable view of every operation a
 * `credential.bind` item grants.
 *
 * The inline {@link OperationsSummary} on a card is a *bounded preview* — it
 * shows a few operations and a count, never the whole list, so a card can't be
 * blown out by a large grant. When the reviewer needs to actually SEE and find
 * a specific operation among many (a 100-operation allow list is realistic),
 * they open this dialog.
 *
 * Impossible to break by design: it's built on the {@link Dialog} primitive (a
 * native `<dialog>` with focus trap + Escape + a body capped to
 * `max-h-[calc(100dvh-2rem)]` that scrolls). No matter the operation count the
 * dialog stays within the viewport — overflow scrolls, it never pushes the
 * header/footer off-screen. A search box filters operations live (matching
 * operationId, method, or path) with a running result count, so finding one op
 * among hundreds is a type-not-scroll task.
 *
 * Read-only: this surfaces what a grant contains; narrowing it is a separate
 * `:amend` concern, never editable here.
 */

const EFFECT_STYLES: Record<
	PermissionRule['effect'],
	{ label: string; chip: string; Icon: typeof ShieldCheck; desc: string }
> = {
	allow: {
		label: 'Allow',
		chip: 'bg-accent-green/10 text-accent-green',
		Icon: ShieldCheck,
		desc: 'The agent can call these operations directly, no human in the loop.',
	},
	deny: {
		label: 'Block',
		chip: 'bg-danger/10 text-danger',
		Icon: ShieldBan,
		desc: 'These operations are always refused — Block overrides everything else.',
	},
	'require-approval': {
		label: 'Needs approval',
		chip: 'bg-accent-orange/10 text-accent-orange',
		Icon: ShieldAlert,
		desc: 'The agent may attempt these, but each call is held and files a new access request for a human to approve before it runs.',
	},
};

/** The fixed broker priority order — strictest first — used to order the legend. */
const EFFECT_ORDER: PermissionRule['effect'][] = ['deny', 'require-approval', 'allow'];

/**
 * A short legend explaining what each effect present in THIS grant means at call
 * time. The broker evaluates rules with a strict priority — `deny` >
 * `require-approval` > `allow`, strictest match wins, no match = implicit deny —
 * so we list only the effects that actually appear, in that priority order
 * (strictest first), and spell out "Needs approval" (the least self-evident
 * tier) explicitly. The fixed-width chip column keeps the descriptions aligned
 * into a tidy second column rather than ragged against variable-width chips.
 */
function EffectLegend({ rules }: { rules: PermissionRule[] }) {
	const present = EFFECT_ORDER.filter((e) => rules.some((r) => r.effect === e));
	if (present.length === 0) return null;
	return (
		<section className="border-border/60 bg-muted/20 rounded-lg border p-3">
			<p className="text-muted-foreground mb-2 text-[10px] font-medium tracking-wide uppercase">
				How these are enforced
				<span className="text-muted-foreground/60 normal-case">
					{' '}
					· strictest match wins
				</span>
			</p>
			<dl className="space-y-2 text-xs">
				{present.map((effect) => {
					const { label, chip, Icon, desc } = EFFECT_STYLES[effect];
					return (
						<div key={effect} className="grid grid-cols-[7rem_1fr] items-start gap-2">
							<dt>
								<span
									className={`inline-flex w-full items-center gap-1 rounded px-1.5 py-0.5 font-semibold ${chip}`}
								>
									<Icon className="h-3 w-3 shrink-0" aria-hidden="true" />
									{label}
								</span>
							</dt>
							<dd className="text-muted-foreground leading-snug">{desc}</dd>
						</div>
					);
				})}
			</dl>
		</section>
	);
}

/** A rule paired with the operations that survive the active search filter. */
interface FilteredRule {
	rule: PermissionRule;
	/** Operations matching the query (or all of them when the query is empty). */
	operations: string[];
	/** Whether the rule's own method/path matched the query (kept even with 0 ops). */
	headerMatched: boolean;
}

function matches(haystack: string | null | undefined, needle: string): boolean {
	return !!haystack && haystack.toLowerCase().includes(needle);
}

/**
 * Filter the rule set against a lowercased query. A rule is kept when either its
 * header (effect label / method / path) matches, or at least one of its
 * operations matches; in the latter case only the matching operations are
 * shown so the reviewer sees exactly what they searched for.
 */
function filterRules(rules: PermissionRule[], query: string): FilteredRule[] {
	const q = query.trim().toLowerCase();
	if (!q) {
		return rules.map((rule) => ({
			rule,
			operations: rule.operations ?? [],
			headerMatched: false,
		}));
	}
	const out: FilteredRule[] = [];
	for (const rule of rules) {
		const ops = rule.operations ?? [];
		const matchedOps = ops.filter((op) => op.toLowerCase().includes(q));
		const headerMatched =
			EFFECT_STYLES[rule.effect].label.toLowerCase().includes(q) ||
			(rule.methods ?? []).some((m) => m.toLowerCase().includes(q)) ||
			matches(rule.path, q);
		if (matchedOps.length > 0 || headerMatched) {
			out.push({
				rule,
				// A header-only match (e.g. searching a method) still shows all the
				// rule's operations so the reviewer sees the rule in full.
				operations: matchedOps.length > 0 ? matchedOps : ops,
				headerMatched,
			});
		}
	}
	return out;
}

function RuleBlock({ filtered }: { filtered: FilteredRule }) {
	const { rule, operations } = filtered;
	const { label, chip, Icon } = EFFECT_STYLES[rule.effect];
	const totalOps = rule.operations?.length ?? 0;
	return (
		<section className="border-border/60 bg-muted/20 rounded-lg border">
			<header className="border-border/40 flex flex-wrap items-center gap-2 border-b px-3 py-2 text-xs">
				<span
					className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 font-semibold ${chip}`}
				>
					<Icon className="h-3 w-3" aria-hidden="true" />
					{label}
				</span>
				{rule.methods?.length ? (
					<span className="text-foreground font-mono">{rule.methods.join(' ')}</span>
				) : !totalOps && !rule.path ? (
					<span className="text-muted-foreground">any request</span>
				) : null}
				{rule.path ? (
					<span className="text-muted-foreground font-mono break-all">{rule.path}</span>
				) : null}
				{totalOps > 0 && (
					<span className="text-muted-foreground/70 ml-auto font-mono text-[10px]">
						{operations.length === totalOps
							? `${totalOps} operation${totalOps === 1 ? '' : 's'}`
							: `${operations.length} of ${totalOps}`}
					</span>
				)}
			</header>
			{operations.length > 0 && (
				<ul className="flex flex-wrap gap-1 p-3">
					{operations.map((op) => (
						<li
							key={op}
							className="bg-muted text-muted-foreground rounded px-1.5 py-0.5 font-mono text-[11px] break-all"
						>
							{op}
						</li>
					))}
				</ul>
			)}
		</section>
	);
}

export interface OperationsDialogProps {
	open: boolean;
	onClose: () => void;
	rules: PermissionRule[];
	/** Optional label of the item the rules belong to, shown as the dialog subtitle. */
	targetLabel?: string;
}

export function OperationsDialog({ open, onClose, rules, targetLabel }: OperationsDialogProps) {
	const [query, setQuery] = useState('');
	const totalOps = useMemo(
		() => rules.reduce((n, r) => n + (r.operations?.length ?? 0), 0),
		[rules],
	);
	const filtered = useMemo(() => filterRules(rules, query), [rules, query]);
	const shownOps = useMemo(
		() => filtered.reduce((n, f) => n + f.operations.length, 0),
		[filtered],
	);

	const subtitleParts = [
		targetLabel,
		`${totalOps} operation${totalOps === 1 ? '' : 's'} across ${rules.length} rule${rules.length === 1 ? '' : 's'}`,
	].filter(Boolean);

	return (
		<Dialog
			open={open}
			onClose={onClose}
			size="lg"
			title="Operations granted"
			subtitle={
				<span className="flex items-center gap-1.5">
					<ListChecks className="h-3 w-3" aria-hidden="true" />
					{subtitleParts.join(' · ')}
				</span>
			}
		>
			<div className="space-y-3">
				{/* What each effect means at call time — explains "Needs approval"
				    rather than leaving the reviewer to guess. */}
				<EffectLegend rules={rules} />

				{totalOps > 8 && (
					<SearchInput
						value={query}
						onValueChange={setQuery}
						size="sm"
						icon={<Filter className="h-3.5 w-3.5" />}
						placeholder="Filter operations, methods, or paths…"
						aria-label="Filter operations"
					/>
				)}

				{/* Screen-reader summary of the whole grant, so SR users get the gist
				    without walking every chip. */}
				<p className="sr-only">{ruleSummary(rules)}</p>

				{query.trim() && (
					<p className="text-muted-foreground text-xs" role="status" aria-live="polite">
						{shownOps === 0
							? `No operations match “${query.trim()}”.`
							: `${shownOps} operation${shownOps === 1 ? '' : 's'} match “${query.trim()}”.`}
					</p>
				)}

				{filtered.length === 0 ? (
					<p className="text-muted-foreground py-6 text-center text-sm">
						No matching operations or rules.
					</p>
				) : (
					<div className="space-y-2.5">
						{filtered.map((f) => (
							<RuleBlock
								key={`${f.rule.effect}:${f.rule.path ?? ''}:${(f.rule.methods ?? []).join(',')}:${(f.rule.operations ?? []).join(',')}`}
								filtered={f}
							/>
						))}
					</div>
				)}
			</div>
		</Dialog>
	);
}
