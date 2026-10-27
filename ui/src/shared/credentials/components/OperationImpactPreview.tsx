/**
 * Operation-impact preview: renders a vendor's operations grouped by
 * first path segment, each op tagged with an ``allow`` / ``partial`` /
 * ``require-approval`` / ``deny`` verdict computed from the caller's rule
 * set via the template-aware matcher (parity-tested against the Python
 * side).
 *
 * Shared between the credentials connect-flow rules page and the
 * Agents page's bindings editor so users author + review rules against
 * the same visualisation everywhere.
 *
 * States:
 * * ``api == null`` or ``version == null`` → "still importing"
 *   skeleton (session hasn't kicked the import yet, or import job is
 *   queued).
 * * fetch returned ``null`` (404) → same skeleton, hook is polling.
 * * fetch returned an empty page → "no operations imported yet" note.
 * * fetch returned data → the grouped preview below.
 */

import { useMemo, useState, type ReactNode } from 'react';
import { ChevronDown, ChevronRight, Loader2 } from 'lucide-react';
import { Badge, Label, MethodBadge } from '@/shared/ui';
import { EffectBadge, effectBadgeProps } from '@/shared/credentials/components/EffectBadge';
import { useVendorOperations } from '@/shared/credentials/api/vendors-hooks';
import type { VendorOperation } from '@/shared/credentials/api/vendors-client';
import type { PermissionRule } from '@/shared/credentials/api/vendors-types';
import { operationText } from '@/shared/credentials/lib/actionRules';
import { PlainText } from '@/shared/credentials/components/PlainText';
import {
	classifyOpCoverage,
	evaluateTemplateOp,
	type OpCoverage,
} from '@/shared/credentials/lib/template-matcher';

export interface OpsApiReference {
	vendor: string;
	name: string | null;
	version: string | null;
}

interface EvaluatedOp {
	op: VendorOperation;
	// Sample-and-classify coverage across concrete instances of the op
	// template. Verdict is ``allow`` / ``require-approval`` / ``deny`` /
	// ``partial``; the ``partial`` case carries concrete sample paths per
	// outcome so the leaf row can expand to show what's really covered.
	coverage: OpCoverage;
}

interface OpGroup {
	// First path segment, e.g. ``/repos``. Root ops (bare ``/``) go
	// under ``/`` so they still get a bucket.
	prefix: string;
	ops: EvaluatedOp[];
	allowedCount: number;
	approvalCount: number;
	deniedCount: number;
}

type HeaderBucket = 'allowed' | 'approval' | 'denied';

/**
 * The group-header bucket an op counts towards: a partial op with any
 * allowed slice counts as allowed, one whose only reachable slice is held
 * counts as needing approval.
 */
function headerBucket(coverage: OpCoverage): HeaderBucket {
	switch (coverage.verdict) {
		case 'allow':
			return 'allowed';
		case 'require-approval':
			return 'approval';
		case 'deny':
			return 'denied';
		case 'partial':
			return coverage.allowedSamples.length > 0 ? 'allowed' : 'approval';
	}
}

/** Leaf sort rank: allowed → partial → ask → denied. */
const VERDICT_RANK: Record<OpCoverage['verdict'], number> = {
	allow: 3,
	partial: 2,
	'require-approval': 1,
	deny: 0,
};

const VERDICT_LABEL: Record<OpCoverage['verdict'], string> = {
	allow: 'allow',
	partial: 'partial',
	'require-approval': 'ask',
	deny: 'deny',
};

/** First path segment or ``/`` when the path has no segments. */
function firstPathSegment(path: string): string {
	const rest = path.startsWith('/') ? path.slice(1) : path;
	if (rest.length === 0) return '/';
	const nextSlash = rest.indexOf('/');
	return `/${nextSlash === -1 ? rest : rest.slice(0, nextSlash)}`;
}

/** An API's operations by what a rule set does with them. */
export interface OperationCounts {
	/** Run without asking (`allow`, or `partial` with an allowed slice). */
	allowed: number;
	/** Held for approval first (`require-approval`, or `partial` whose only way in is held). */
	ask: number;
	/** Never run. */
	denied: number;
	/** Of `denied`: matched by no rule at all — not set, so denied by default. */
	notSet: number;
	total: number;
}

/** Tally operations into the preview's own header buckets. */
export function countOperations(
	rules: readonly PermissionRule[],
	items: readonly VendorOperation[],
): OperationCounts {
	const counts: OperationCounts = {
		allowed: 0,
		ask: 0,
		denied: 0,
		notSet: 0,
		total: items.length,
	};
	for (const op of items) {
		const bucket = headerBucket(classifyOpCoverage(rules, op));
		if (bucket === 'allowed') counts.allowed += 1;
		else if (bucket === 'approval') counts.ask += 1;
		else {
			counts.denied += 1;
			if (evaluateTemplateOp(rules, op).matchingRule === null) counts.notSet += 1;
		}
	}
	return counts;
}

/**
 * How a binding's rules treat an API's operations — allowed, ask, denied, as
 * the preview's group headers count them — over the operation list, with the
 * same client-side matcher the preview runs. An Ask operation is never counted
 * as allowed: it runs only once someone approves it.
 *
 * - `undefined` while the first read is in flight, or while `rules` is unknown.
 * - `null` when there is nothing to judge: the reference names no single
 *   version, or a read has finished without an operation list (the import
 *   isn't available yet — the hook keeps polling, and the count arrives with
 *   it — or the read failed).
 */
export function useAllowedOperationCount(
	api: OpsApiReference | null,
	rules: readonly PermissionRule[] | undefined,
	opts: { enabled?: boolean } = {},
): OperationCounts | null | undefined {
	const ready = !!api && !!api.name && !!api.version;
	const ops = useVendorOperations(api ?? undefined, { enabled: (opts.enabled ?? true) && ready });
	const unavailable = ops.isError || (ops.isFetched && ops.data === null);
	return useMemo(() => {
		if (!ready || unavailable) return null;
		if (ops.data == null || rules === undefined) return undefined;
		return countOperations(rules, ops.data.data);
	}, [ready, unavailable, ops.data, rules]);
}

/**
 * "12 denied (4 chosen, 8 not set)" — denied outcomes, split when some are
 * denied only because no rule matches them, so the number never reads as
 * the count of Deny choices.
 */
export function deniedText(counts: Pick<OperationCounts, 'denied' | 'notSet'>): string {
	const n = counts.denied.toLocaleString();
	if (counts.notSet === 0 || counts.denied === 0) return `${n} denied`;
	if (counts.notSet === counts.denied) return `${n} denied (not set)`;
	return `${n} denied (${(counts.denied - counts.notSet).toLocaleString()} chosen, ${counts.notSet.toLocaleString()} not set)`;
}

/** "3 allowed · 1 ask · 12 denied (4 chosen, 8 not set)" — always all three, so
 * an Ask is never folded into allowed. */
export function operationCountsText(counts: OperationCounts): string {
	return [
		`${counts.allowed.toLocaleString()} allowed`,
		`${counts.ask.toLocaleString()} ask`,
		deniedText(counts),
	].join(' · ');
}

export function OperationImpactPreview({
	api,
	rules,
	label = 'What this credential lets an agent do',
	showTotals = true,
}: {
	api: OpsApiReference | null;
	rules: readonly PermissionRule[];
	/**
	 * Section label above the preview. Overridable for callers where
	 * the default copy doesn't fit (the Agents page's per-binding editor
	 * uses "Effective access for this binding" instead of the
	 * connect-flow's copy).
	 */
	label?: ReactNode;
	/** The totals beside the label; off where a bar above already states them. */
	showTotals?: boolean;
}) {
	const ops = useVendorOperations(api ?? undefined, { enabled: !!api });
	const items = useMemo(() => ops.data?.data ?? [], [ops.data]);
	const importing = !api || !api.name || !api.version || ops.data == null;

	// Flat top-level grouping by first path segment. A deep hierarchical
	// tree was more accurate but harder to navigate on real APIs like
	// GitHub — most useful high-level buckets are a single segment
	// (``/repos``, ``/users``, …) and nesting beyond that just adds
	// clicks. Partial ops carry their own per-row disclosure to expose
	// the concrete allow/deny slice.
	const groups = useMemo<OpGroup[]>(() => {
		if (items.length === 0) return [];
		const byPrefix = new Map<string, EvaluatedOp[]>();
		for (const op of items) {
			const coverage = classifyOpCoverage(rules, {
				method: op.method,
				path: op.path,
				operation_id: op.operation_id,
			});
			const entry: EvaluatedOp = { op, coverage };
			const key = firstPathSegment(op.path);
			const bucket = byPrefix.get(key);
			if (bucket) bucket.push(entry);
			else byPrefix.set(key, [entry]);
		}
		const out: OpGroup[] = [];
		for (const [prefix, entries] of byPrefix) {
			entries.sort(
				(a, b) => VERDICT_RANK[b.coverage.verdict] - VERDICT_RANK[a.coverage.verdict],
			);
			const counts: Record<HeaderBucket, number> = { allowed: 0, approval: 0, denied: 0 };
			for (const e of entries) counts[headerBucket(e.coverage)] += 1;
			out.push({
				prefix,
				ops: entries,
				allowedCount: counts.allowed,
				approvalCount: counts.approval,
				deniedCount: counts.denied,
			});
		}
		out.sort((a, b) => {
			// Groups an agent can reach at all (allowed or held) before
			// all-denied groups, then the most allowed, then the most held.
			const aReach = a.allowedCount + a.approvalCount > 0;
			const bReach = b.allowedCount + b.approvalCount > 0;
			if (aReach !== bReach) return aReach ? -1 : 1;
			if (a.allowedCount !== b.allowedCount) return b.allowedCount - a.allowedCount;
			if (a.approvalCount !== b.approvalCount) return b.approvalCount - a.approvalCount;
			return a.prefix.localeCompare(b.prefix);
		});
		return out;
	}, [items, rules]);

	// Only where they're shown: a host with its own bar would pay a second
	// pass over every operation for nothing.
	const totals = useMemo(
		() => (showTotals ? countOperations(rules, items) : null),
		[showTotals, rules, items],
	);

	return (
		<div className="space-y-2">
			<div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
				<Label>{label}</Label>
				{totals && groups.length > 0 && (
					<span
						className="text-foreground-sub text-xs tabular-nums"
						data-testid="op-impact-totals"
					>
						{operationCountsText(totals)}
					</span>
				)}
			</div>
			{importing ? (
				<div className="bg-field rounded-lg px-3 py-6 text-center">
					<Loader2 className="text-muted-foreground mx-auto h-4 w-4 animate-spin" />
					<p className="text-foreground-sub mt-2 text-xs">
						Operations still importing — this preview will fill in shortly.
					</p>
				</div>
			) : groups.length === 0 ? (
				<div className="bg-field rounded-lg px-3 py-4 text-center">
					<p className="text-foreground-sub text-xs">
						No operations imported for this vendor yet.
					</p>
				</div>
			) : (
				<div className="max-h-96 space-y-1 overflow-y-auto rounded-lg">
					{groups.map((g) => (
						<OperationImpactGroup key={g.prefix} group={g} />
					))}
				</div>
			)}
		</div>
	);
}

/**
 * One top-level path-prefix group. Starts collapsed — the aggregate
 * ``X allowed / Y ask / Z denied`` counts on the header let
 * the user skim without expanding. Ops inside are sorted allowed →
 * partial → ask → denied.
 */
function OperationImpactGroup({ group }: { group: OpGroup }) {
	const [open, setOpen] = useState(false);
	return (
		// Borderless group card; `overflow-hidden` clips the header's hover fill
		// to the same curve, and the leaf rows use the inner radius (12 − 6px).
		<div className="bg-field overflow-hidden rounded-lg">
			<button
				type="button"
				onClick={(): void => setOpen((v) => !v)}
				className="hover:bg-tint-2 focus-visible:ring-ring flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-xs transition-colors focus-visible:ring-2 focus-visible:outline-none focus-visible:ring-inset"
				aria-expanded={open}
			>
				{open ? (
					<ChevronDown className="text-muted-foreground h-3.5 w-3.5 shrink-0" />
				) : (
					<ChevronRight className="text-muted-foreground h-3.5 w-3.5 shrink-0" />
				)}
				<span className="text-foreground-name truncate font-mono text-[11px]">
					{group.prefix}/
				</span>
				<span className="ml-auto flex items-center gap-1.5">
					{group.allowedCount > 0 && (
						<Badge variant="success">{group.allowedCount} allowed</Badge>
					)}
					{group.approvalCount > 0 && (
						<EffectBadge effect="require-approval">
							{group.approvalCount} ask
						</EffectBadge>
					)}
					{group.deniedCount > 0 && (
						<Badge variant="danger">{group.deniedCount} denied</Badge>
					)}
				</span>
			</button>
			{open && (
				<div className="space-y-0.5 p-1.5 pt-0">
					{group.ops.map(({ op, coverage }) => (
						<OperationImpactLeafRow key={op.operation_id} op={op} coverage={coverage} />
					))}
				</div>
			)}
		</div>
	);
}

/**
 * Leaf op row rendered inside a group's expanded body. Verdict pill is
 * one of ``allow`` / ``partial`` / ``ask`` / ``deny``.
 *
 * ``partial`` rows carry a caret and start COLLAPSED — clicking the row
 * expands follow-up lines with one concrete sample per outcome:
 *   ``ALLOW e.g. /repos/jentic/jentic-one/commits``
 *   ``DENY  e.g. /repos/example-owner/example-repo/commits``
 * Inline-expanded partial detail dominated the group; hiding it behind
 * a click keeps the surface scannable while still letting the user
 * drill into the "narrow slice of a broader op" case that partial
 * exists to signal.
 */
function OperationImpactLeafRow({ op, coverage }: { op: VendorOperation; coverage: OpCoverage }) {
	const [expanded, setExpanded] = useState(false);
	const verdictClass = 'h-5 w-[58px] shrink-0 justify-center rounded-[5px]';
	// Partial is no single effect: it keeps Badge's plain warning chip.
	const verdictBadge =
		coverage.verdict === 'partial'
			? { variant: 'warning' as const, className: verdictClass }
			: effectBadgeProps(coverage.verdict, verdictClass);
	const verdictLabel = VERDICT_LABEL[coverage.verdict];
	const allowExample = coverage.allowedSamples[0];
	const approvalExample = coverage.approvalSamples[0];
	const denyExample = coverage.deniedSamples[0];
	const canExpand =
		coverage.verdict === 'partial' && Boolean(allowExample || approvalExample || denyExample);
	const rowInteractive = canExpand ? 'cursor-pointer hover:bg-tint-2 transition-colors' : '';
	return (
		<div
			className={`rounded-[6px] px-2.5 py-1 text-xs ${rowInteractive}`}
			data-testid="op-impact-row"
			onClick={canExpand ? (): void => setExpanded((v) => !v) : undefined}
		>
			<div className="flex items-center gap-2.5">
				{canExpand &&
					(expanded ? (
						<ChevronDown className="text-muted-foreground h-3 w-3 shrink-0" />
					) : (
						<ChevronRight className="text-muted-foreground h-3 w-3 shrink-0" />
					))}
				<Badge {...verdictBadge} aria-label={verdictLabel}>
					{verdictLabel}
				</Badge>
				<MethodBadge method={op.method} />
				<span className="text-foreground-name truncate font-mono text-[11px]">
					{op.path}
				</span>
				<PlainText value={operationText(op)} className="ml-auto text-[10px]" />
			</div>
			{expanded && canExpand && (
				<div className="mt-0.5 space-y-0.5 pl-6 font-mono text-[10px]">
					{allowExample && (
						<div className="flex items-center gap-1.5">
							<Badge variant="success" className="px-1.5 py-0 font-sans">
								allow
							</Badge>
							<span className="text-muted-foreground">e.g.</span>
							<span className="text-foreground/80 truncate">{allowExample}</span>
						</div>
					)}
					{approvalExample && (
						<div className="flex items-center gap-1.5">
							<EffectBadge
								effect="require-approval"
								className="px-1.5 py-0 font-sans"
							>
								ask
							</EffectBadge>
							<span className="text-muted-foreground">e.g.</span>
							<span className="text-foreground/80 truncate">{approvalExample}</span>
						</div>
					)}
					{denyExample && (
						<div className="flex items-center gap-1.5">
							<Badge variant="danger" className="px-1.5 py-0 font-sans">
								deny
							</Badge>
							<span className="text-muted-foreground">e.g.</span>
							<span className="text-foreground/80 truncate">{denyExample}</span>
						</div>
					)}
				</div>
			)}
		</div>
	);
}
