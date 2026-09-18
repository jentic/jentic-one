/**
 * Operation-impact preview: renders a vendor's operations grouped by
 * first path segment, each op tagged with an ``allow`` / ``partial`` /
 * ``deny`` verdict computed from the caller's rule set via the
 * template-aware matcher (parity-tested against the Python side).
 *
 * Shared between the credentials connect-flow rules page and the
 * agent-detail bindings editor so users author + review rules against
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

import { useMemo, useState } from 'react';
import { ChevronDown, ChevronRight, Loader2 } from 'lucide-react';
import { Label } from '@/shared/ui';
import { useVendorOperations } from '@/shared/credentials/api/vendors-hooks';
import type { VendorOperation } from '@/shared/credentials/api/vendors-client';
import type { PermissionRule } from '@/shared/credentials/api/vendors-types';
import { classifyOpCoverage, type OpCoverage } from '@/shared/credentials/lib/template-matcher';

export interface OpsApiReference {
	vendor: string;
	name: string | null;
	version: string | null;
}

interface EvaluatedOp {
	op: VendorOperation;
	// Sample-and-classify coverage across concrete instances of the op
	// template. Verdict is ``allow`` / ``deny`` / ``partial``; the
	// ``partial`` case carries concrete allowed + denied sample paths so
	// the leaf row can expand to show what's really covered.
	coverage: OpCoverage;
}

interface OpGroup {
	// First path segment, e.g. ``/repos``. Root ops (bare ``/``) go
	// under ``/`` so they still get a bucket.
	prefix: string;
	ops: EvaluatedOp[];
	allowedCount: number;
	deniedCount: number;
}

/** First path segment or ``/`` when the path has no segments. */
function firstPathSegment(path: string): string {
	const rest = path.startsWith('/') ? path.slice(1) : path;
	if (rest.length === 0) return '/';
	const nextSlash = rest.indexOf('/');
	return `/${nextSlash === -1 ? rest : rest.slice(0, nextSlash)}`;
}

export function OperationImpactPreview({
	api,
	rules,
	label = 'What this credential lets an agent do',
}: {
	api: OpsApiReference | null;
	rules: readonly PermissionRule[];
	/**
	 * Section label above the preview. Overridable for callers where
	 * the default copy doesn't fit (agent-detail per-binding editor
	 * uses "Effective access for this binding" instead of the
	 * connect-flow's copy).
	 */
	label?: string;
}) {
	const ops = useVendorOperations(api ?? undefined, { enabled: !!api });
	const items = ops.data?.data ?? [];
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
		const opRank = (op: EvaluatedOp): number =>
			op.coverage.verdict === 'allow' ? 2 : op.coverage.verdict === 'partial' ? 1 : 0;
		const out: OpGroup[] = [];
		for (const [prefix, entries] of byPrefix) {
			entries.sort((a, b) => opRank(b) - opRank(a));
			const allowedCount = entries.filter((e) => e.coverage.verdict !== 'deny').length;
			out.push({
				prefix,
				ops: entries,
				allowedCount,
				deniedCount: entries.length - allowedCount,
			});
		}
		out.sort((a, b) => {
			// Groups with any allowed ops before all-denied groups.
			if (a.allowedCount > 0 !== b.allowedCount > 0) {
				return a.allowedCount > 0 ? -1 : 1;
			}
			if (a.allowedCount !== b.allowedCount) return b.allowedCount - a.allowedCount;
			return a.prefix.localeCompare(b.prefix);
		});
		return out;
	}, [items, rules]);

	return (
		<div className="space-y-2">
			<Label>{label}</Label>
			{importing ? (
				<div className="border-border bg-muted/20 rounded-lg border px-3 py-6 text-center">
					<Loader2 className="text-muted-foreground mx-auto h-4 w-4 animate-spin" />
					<p className="text-muted-foreground mt-2 text-xs">
						Operations still importing — this preview will fill in shortly.
					</p>
				</div>
			) : groups.length === 0 ? (
				<div className="border-border bg-muted/20 rounded-lg border px-3 py-4 text-center">
					<p className="text-muted-foreground text-xs">
						No operations imported for this vendor yet.
					</p>
				</div>
			) : (
				<div className="border-border max-h-96 space-y-1 overflow-y-auto rounded-lg border p-2">
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
 * ``X allowed / Y denied`` counts on the header let the user skim
 * without expanding. Ops inside are sorted allowed → partial → denied.
 */
function OperationImpactGroup({ group }: { group: OpGroup }) {
	const [open, setOpen] = useState(false);
	return (
		<div className="border-border bg-background rounded-md border">
			<button
				type="button"
				onClick={(): void => setOpen((v) => !v)}
				className="hover:bg-muted/40 flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-xs transition-colors"
				aria-expanded={open}
			>
				{open ? (
					<ChevronDown className="text-muted-foreground h-3.5 w-3.5 shrink-0" />
				) : (
					<ChevronRight className="text-muted-foreground h-3.5 w-3.5 shrink-0" />
				)}
				<span className="text-foreground truncate font-mono text-[11px]">
					{group.prefix}/
				</span>
				<span className="ml-auto flex items-center gap-1.5">
					{group.allowedCount > 0 && (
						<span className="bg-success/10 text-success border-success/40 rounded-md border px-1.5 py-0.5 font-mono text-[10px]">
							{group.allowedCount} allowed
						</span>
					)}
					{group.deniedCount > 0 && (
						<span className="bg-danger/10 text-danger border-danger/40 rounded-md border px-1.5 py-0.5 font-mono text-[10px]">
							{group.deniedCount} denied
						</span>
					)}
				</span>
			</button>
			{open && (
				<div className="border-border space-y-1 border-t p-1.5">
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
 * one of ``allow`` / ``partial`` / ``deny``.
 *
 * ``partial`` rows carry a caret and start COLLAPSED — clicking the row
 * expands two follow-up lines with one concrete sample per bucket:
 *   ``ALLOW e.g. /repos/jentic/jentic-one/commits``
 *   ``DENY  e.g. /repos/example-owner/example-repo/commits``
 * Inline-expanded partial detail dominated the group; hiding it behind
 * a click keeps the surface scannable while still letting the user
 * drill into the "narrow slice of a broader op" case that partial
 * exists to signal.
 */
function OperationImpactLeafRow({ op, coverage }: { op: VendorOperation; coverage: OpCoverage }) {
	const [expanded, setExpanded] = useState(false);
	const verdictPill =
		coverage.verdict === 'allow'
			? 'bg-success/10 text-success border-success/40'
			: coverage.verdict === 'partial'
				? 'bg-warning/10 text-warning border-warning/40'
				: 'bg-danger/10 text-danger border-danger/40';
	const verdictLabel = coverage.verdict;
	const allowExample = coverage.allowedSamples[0];
	const denyExample = coverage.deniedSamples[0];
	const canExpand = coverage.verdict === 'partial' && Boolean(allowExample || denyExample);
	const rowInteractive = canExpand ? 'cursor-pointer hover:bg-muted/40 transition-colors' : '';
	return (
		<div
			className={`bg-muted/20 border-border rounded-md border px-2.5 py-1 text-xs ${rowInteractive}`}
			onClick={canExpand ? (): void => setExpanded((v) => !v) : undefined}
		>
			<div className="flex items-center gap-2.5">
				{canExpand &&
					(expanded ? (
						<ChevronDown className="text-muted-foreground h-3 w-3 shrink-0" />
					) : (
						<ChevronRight className="text-muted-foreground h-3 w-3 shrink-0" />
					))}
				<span
					className={`rounded-md border px-1.5 py-0.5 font-mono text-[10px] uppercase ${verdictPill}`}
					aria-label={verdictLabel}
				>
					{verdictLabel}
				</span>
				<span className="text-muted-foreground font-mono text-[10px] uppercase">
					{op.method}
				</span>
				<span className="text-foreground truncate font-mono text-[11px]">{op.path}</span>
				{op.name && (
					<span className="text-muted-foreground ml-auto truncate text-[10px]">
						{op.name}
					</span>
				)}
			</div>
			{expanded && canExpand && (
				<div className="mt-0.5 space-y-0.5 pl-6 font-mono text-[10px]">
					{allowExample && (
						<div className="flex items-center gap-1.5">
							<span className="bg-success/10 text-success border-success/40 rounded border px-1 py-0 text-[9px] uppercase">
								allow
							</span>
							<span className="text-muted-foreground">e.g.</span>
							<span className="text-foreground/80 truncate">{allowExample}</span>
						</div>
					)}
					{denyExample && (
						<div className="flex items-center gap-1.5">
							<span className="bg-danger/10 text-danger border-danger/40 rounded border px-1 py-0 text-[9px] uppercase">
								deny
							</span>
							<span className="text-muted-foreground">e.g.</span>
							<span className="text-foreground/80 truncate">{denyExample}</span>
						</div>
					)}
				</div>
			)}
		</div>
	);
}
