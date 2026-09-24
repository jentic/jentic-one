/**
 * SpecViewerDialog — view a workspace API revision's OpenAPI document, as a
 * DIFF by default (when the caller supplies a base).
 *
 * The old viewer dumped the whole ~800-line resolved JSON, leaving reviewers
 * to eyeball what changed. Now, when the caller supplies a comparison base
 * (`diffAgainst` — the previous revision, matching the row summary's "vs
 * previous" delta), the dialog opens in diff mode: a structural before/after
 * list of exactly the changed sections (`$.servers`, …), with a "Full spec"
 * toggle for the raw document. Entry points whose label promises the raw
 * document (the header's "View spec") pass `defaultMode="full"` instead.
 * Both documents are fetched lazily behind the open flag
 * (`useApiSpec(key, open)`), so nothing large loads on the detail page
 * itself.
 */
import { useEffect, useMemo, useState } from 'react';
import { Download } from 'lucide-react';
import {
	Dialog,
	Button,
	Skeleton,
	ErrorAlert,
	CopyButton,
	SegmentedToggle,
	Badge,
} from '@/shared/ui';
import { useApiSpec, formatApiKey, diffSpecs } from '@/modules/workspace/api';
import type { ApiKey, SpecDiffBase, SpecDiffEntry } from '@/modules/workspace/api';

export type { SpecDiffBase } from '@/modules/workspace/api';

export interface SpecViewerDialogProps {
	apiKey: ApiKey;
	open: boolean;
	onClose: () => void;
	/**
	 * View a specific revision's spec (old/archived or draft/pending). When
	 * omitted, the live revision's spec is shown.
	 */
	revisionId?: string | null;
	/** Short label (e.g. revision id / state) shown beside the api key. */
	revisionLabel?: string;
	/**
	 * Comparison base. When present the dialog offers a Diff/Full-spec toggle
	 * (diff of `diffAgainst.revisionId` → `revisionId`); when absent (e.g. the
	 * API's first revision) only the full spec is shown.
	 */
	diffAgainst?: SpecDiffBase | null;
	/**
	 * Which view opens first when a diff base exists. `'diff'` (default) for
	 * Diff-labeled entry points; pass `'full'` when the trigger's label
	 * promises the raw document (e.g. the header "View spec" button).
	 */
	defaultMode?: 'diff' | 'full';
}

const KIND_VARIANT = { added: 'success', removed: 'danger', changed: 'warning' } as const;

function pretty(value: unknown): string {
	try {
		return JSON.stringify(value, null, 2);
	} catch {
		return String(value);
	}
}

function DiffEntryBlock({ entry }: { entry: SpecDiffEntry }) {
	return (
		<li className="border-border/60 rounded-lg border p-3" data-testid="spec-diff-entry">
			<div className="mb-2 flex items-center gap-2">
				<Badge variant={KIND_VARIANT[entry.kind]}>{entry.kind}</Badge>
				<code className="text-foreground font-mono text-xs break-all">{entry.path}</code>
			</div>
			<div className="space-y-1.5">
				{entry.kind !== 'added' ? (
					// tabIndex: a scrollable region with no focusable child is not
					// keyboard-scrollable in Safari/older Chromium. The sr-only label
					// carries the before/after direction that color + `-`/`+` prefixes
					// alone don't announce.
					<pre
						tabIndex={0}
						aria-label={`Before, at ${entry.path}`}
						className="bg-danger/8 border-danger/20 text-foreground overflow-auto rounded border p-2 font-mono text-xs leading-relaxed whitespace-pre"
					>
						<span className="sr-only">Before: </span>
						{`- ${pretty(entry.before).split('\n').join('\n- ')}`}
					</pre>
				) : null}
				{entry.kind !== 'removed' ? (
					<pre
						tabIndex={0}
						aria-label={`After, at ${entry.path}`}
						className="bg-success/8 border-success/20 text-foreground overflow-auto rounded border p-2 font-mono text-xs leading-relaxed whitespace-pre"
					>
						<span className="sr-only">After: </span>
						{`+ ${pretty(entry.after).split('\n').join('\n+ ')}`}
					</pre>
				) : null}
			</div>
		</li>
	);
}

export function SpecViewerDialog({
	apiKey,
	open,
	onClose,
	revisionId,
	revisionLabel,
	diffAgainst,
	defaultMode = 'diff',
}: SpecViewerDialogProps) {
	const hasDiff = diffAgainst != null;
	const initialMode = hasDiff ? defaultMode : 'full';
	// View mode is a transient flag, not a draft — reset to the default on
	// every open, per the dialog state-lifecycle rule.
	const [mode, setMode] = useState<'diff' | 'full'>(initialMode);
	useEffect(() => {
		if (open) setMode(initialMode);
	}, [open, initialMode]);

	const query = useApiSpec(apiKey, open, revisionId);
	const baseQuery = useApiSpec(
		apiKey,
		open && hasDiff && mode === 'diff',
		diffAgainst?.revisionId,
	);

	const prettySpec = useMemo(() => (query.data == null ? '' : pretty(query.data)), [query.data]);

	const diff = useMemo(() => {
		if (mode !== 'diff' || query.data == null || baseQuery.data == null) return null;
		return diffSpecs(baseQuery.data, query.data);
	}, [mode, query.data, baseQuery.data]);

	function download() {
		const blob = new Blob([prettySpec], { type: 'application/json' });
		const url = URL.createObjectURL(blob);
		const a = document.createElement('a');
		const revSuffix = revisionId ? `-${revisionId.slice(0, 8)}` : '';
		a.href = url;
		a.download = `${apiKey.vendor}-${apiKey.name}-${apiKey.version}${revSuffix}.openapi.json`;
		a.click();
		URL.revokeObjectURL(url);
	}

	const isLoading = query.isLoading || (mode === 'diff' && hasDiff && baseQuery.isLoading);
	const error = query.isError
		? query.error
		: mode === 'diff' && baseQuery.isError
			? baseQuery.error
			: null;

	const panelId = mode === 'diff' ? 'spec-view-panel-diff' : 'spec-view-panel-full';
	const tabId = (value: string) => `spec-view-tab-${value}`;

	return (
		<Dialog
			open={open}
			onClose={onClose}
			title={revisionLabel ? `OpenAPI spec · ${revisionLabel}` : 'OpenAPI spec'}
			size="lg"
			footer={
				<>
					<Button variant="ghost" size="sm" onClick={onClose}>
						Close
					</Button>
					{prettySpec ? (
						// "full spec" in the labels because in diff mode these still act
						// on the whole target document, not the entries on screen.
						<>
							<CopyButton value={prettySpec} label="Copy full spec" />
							<Button variant="secondary" size="sm" onClick={download}>
								<Download size={14} aria-hidden="true" />
								Download full spec
							</Button>
						</>
					) : null}
				</>
			}
		>
			<div className="mb-3 flex flex-wrap items-center justify-between gap-2">
				<p className="text-muted-foreground font-mono text-xs">{formatApiKey(apiKey)}</p>
				{hasDiff ? (
					<SegmentedToggle
						as="tabs"
						ariaLabel="Spec view"
						getTabId={tabId}
						getControls={(value) => `spec-view-panel-${value}`}
						options={[
							{ value: 'diff', label: `Diff vs ${diffAgainst.label}` },
							{ value: 'full', label: 'Full spec' },
						]}
						value={mode}
						onChange={setMode}
					/>
				) : null}
			</div>
			{isLoading ? (
				<div role="status" aria-live="polite" aria-busy="true" className="space-y-2">
					<span className="sr-only">Loading spec…</span>
					{Array.from({ length: 8 }).map((_, i) => (
						<Skeleton key={i} className="h-4 w-full" />
					))}
				</div>
			) : error != null ? (
				<div className="space-y-3">
					<ErrorAlert
						message={error instanceof Error ? error : 'Failed to load the spec.'}
					/>
					<Button
						variant="secondary"
						size="sm"
						onClick={() => {
							void query.refetch();
							if (hasDiff) void baseQuery.refetch();
						}}
					>
						Try again
					</Button>
				</div>
			) : mode === 'diff' && diff != null ? (
				diff.entries.length === 0 ? (
					<p
						id={panelId}
						role="tabpanel"
						aria-labelledby={tabId('diff')}
						className="text-muted-foreground text-sm"
						data-testid="spec-diff-empty"
					>
						No differences vs {diffAgainst?.label}.
					</p>
				) : (
					<div
						id={panelId}
						role="tabpanel"
						aria-labelledby={tabId('diff')}
						tabIndex={0}
						aria-label="Spec changes"
						className="max-h-[60vh] overflow-auto"
						data-testid="spec-diff-content"
					>
						<p className="text-muted-foreground mb-2 text-xs">
							{diff.entries.length}
							{diff.truncated ? '+' : ''} changed section
							{diff.entries.length === 1 && !diff.truncated ? '' : 's'} vs{' '}
							{diffAgainst?.label}
							{diff.truncated ? ' (list truncated)' : ''}
						</p>
						<ul className="space-y-2">
							{diff.entries.map((entry) => (
								<DiffEntryBlock key={`${entry.kind}:${entry.path}`} entry={entry} />
							))}
						</ul>
					</div>
				)
			) : (
				<pre
					id={hasDiff ? panelId : undefined}
					role={hasDiff ? 'tabpanel' : undefined}
					aria-labelledby={hasDiff ? tabId('full') : undefined}
					tabIndex={0}
					aria-label="Full spec JSON"
					className="bg-muted/40 border-border/60 text-foreground max-h-[60vh] overflow-auto rounded-lg border p-3 font-mono text-xs leading-relaxed whitespace-pre"
					data-testid="spec-viewer-content"
				>
					{prettySpec}
				</pre>
			)}
		</Dialog>
	);
}
