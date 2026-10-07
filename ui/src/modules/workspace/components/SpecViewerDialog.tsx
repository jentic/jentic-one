/**
 * SpecViewerDialog — view a workspace API revision's OpenAPI document, as a
 * DIFF by default (when the caller supplies a base).
 *
 * When the caller supplies a comparison base
 * (`diffAgainst` — the previous revision, matching the row summary's "vs
 * previous" delta), the dialog opens in diff mode: a structural before/after
 * list of exactly the changed sections (`$.servers`, …), with a "Full spec"
 * toggle for the raw document. The hub's Spec tab (`SpecViewerPanel`) shares
 * the same viewer but opens on the full document.
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
import { cn } from '@/shared/lib/utils';
import { useApiSpec, formatApiKey, diffSpecs } from '@/modules/workspace/api';
import type { ApiKey, SpecDiffBase, SpecDiffEntry } from '@/modules/workspace/api';

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
		<li className="bg-field rounded-lg p-3" data-testid="spec-diff-entry">
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
						className="bg-danger/8 text-foreground overflow-auto rounded-md p-2 font-mono text-xs leading-relaxed whitespace-pre"
					>
						<span className="sr-only">Before: </span>
						{`- ${pretty(entry.before).split('\n').join('\n- ')}`}
					</pre>
				) : null}
				{entry.kind !== 'removed' ? (
					<pre
						tabIndex={0}
						aria-label={`After, at ${entry.path}`}
						className="bg-success/8 text-foreground overflow-auto rounded-md p-2 font-mono text-xs leading-relaxed whitespace-pre"
					>
						<span className="sr-only">After: </span>
						{`+ ${pretty(entry.after).split('\n').join('\n+ ')}`}
					</pre>
				) : null}
			</div>
		</li>
	);
}

interface SpecViewState {
	mode: 'diff' | 'full';
	setMode: (mode: 'diff' | 'full') => void;
	hasDiff: boolean;
	prettySpec: string;
	diff: ReturnType<typeof diffSpecs> | null;
	isLoading: boolean;
	error: unknown;
	retry: () => void;
	download: () => void;
}

/**
 * The viewer's state — shared by the dialog and the inline panel (the API
 * hub's Spec tab), so both fetch, diff and download the same way. `active`
 * gates the (potentially large) spec fetches; the view mode is a transient
 * flag, reset to the default whenever the viewer (re)activates, per the dialog
 * state-lifecycle rule.
 */
function useSpecView({
	apiKey,
	active,
	revisionId,
	diffAgainst,
	defaultMode = 'diff',
}: {
	apiKey: ApiKey;
	active: boolean;
	revisionId?: string | null;
	diffAgainst?: SpecDiffBase | null;
	defaultMode?: 'diff' | 'full';
}): SpecViewState {
	const hasDiff = diffAgainst != null;
	const initialMode = hasDiff ? defaultMode : 'full';
	const [mode, setMode] = useState<'diff' | 'full'>(initialMode);
	useEffect(() => {
		if (active) setMode(initialMode);
	}, [active, initialMode]);

	const query = useApiSpec(apiKey, active, revisionId);
	const baseQuery = useApiSpec(
		apiKey,
		active && hasDiff && mode === 'diff',
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

	return {
		mode,
		setMode,
		hasDiff,
		prettySpec,
		diff,
		isLoading,
		error,
		retry: () => {
			void query.refetch();
			if (hasDiff) void baseQuery.refetch();
		},
		download,
	};
}

function SpecActions({ view }: { view: SpecViewState }) {
	if (!view.prettySpec) return null;
	// "full spec" in the labels because in diff mode these still act on the
	// whole target document, not the entries on screen.
	return (
		<>
			<CopyButton value={view.prettySpec} label="Copy full spec" />
			<Button variant="secondary" size="sm" onClick={view.download}>
				<Download size={14} aria-hidden="true" />
				Download full spec
			</Button>
		</>
	);
}

interface SpecBodyTestIds {
	content: string;
	diffContent: string;
	diffEmpty: string;
}

const DIALOG_TEST_IDS: SpecBodyTestIds = {
	content: 'spec-viewer-content',
	diffContent: 'spec-diff-content',
	diffEmpty: 'spec-diff-empty',
};

const PANEL_TEST_IDS: SpecBodyTestIds = {
	content: 'spec-panel-content',
	diffContent: 'spec-panel-diff-content',
	diffEmpty: 'spec-panel-diff-empty',
};

function SpecViewBody({
	apiKey,
	view,
	diffAgainst,
	idPrefix,
	testIds = DIALOG_TEST_IDS,
	maxHeightClass = 'max-h-[60vh]',
}: {
	apiKey: ApiKey;
	view: SpecViewState;
	diffAgainst?: SpecDiffBase | null;
	idPrefix: string;
	/** Distinct per surface, so the inline panel and the dialog never collide. */
	testIds?: SpecBodyTestIds;
	maxHeightClass?: string;
}) {
	const { mode, setMode, hasDiff, prettySpec, diff, isLoading, error } = view;
	const panelId = mode === 'diff' ? `${idPrefix}-panel-diff` : `${idPrefix}-panel-full`;
	const tabId = (value: string) => `${idPrefix}-tab-${value}`;

	return (
		<>
			<div className="mb-3 flex flex-wrap items-center justify-between gap-2">
				<p className="text-muted-foreground font-mono text-xs">{formatApiKey(apiKey)}</p>
				{hasDiff ? (
					<SegmentedToggle
						as="tabs"
						ariaLabel="Spec view"
						getTabId={tabId}
						getControls={(value) => `${idPrefix}-panel-${value}`}
						options={[
							{ value: 'diff', label: `Diff vs ${diffAgainst?.label}` },
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
					<Button variant="secondary" size="sm" onClick={view.retry}>
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
						data-testid={testIds.diffEmpty}
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
						className={cn(maxHeightClass, 'overflow-auto')}
						data-testid={testIds.diffContent}
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
					className={cn(
						'bg-field text-foreground overflow-auto rounded-lg p-3 font-mono text-xs leading-relaxed whitespace-pre',
						maxHeightClass,
					)}
					data-testid={testIds.content}
				>
					{prettySpec}
				</pre>
			)}
		</>
	);
}

export function SpecViewerDialog({
	apiKey,
	open,
	onClose,
	revisionId,
	revisionLabel,
	diffAgainst,
}: SpecViewerDialogProps) {
	const view = useSpecView({ apiKey, active: open, revisionId, diffAgainst });

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
					{open ? <SpecActions view={view} /> : null}
				</>
			}
		>
			{/* Only mounted while open: a closed native <dialog> still renders its
			    children, so an always-mounted body would keep a full document (and
			    its ids/test ids) on the page. */}
			{open ? (
				<SpecViewBody
					apiKey={apiKey}
					view={view}
					diffAgainst={diffAgainst}
					idPrefix="spec-view"
				/>
			) : null}
		</Dialog>
	);
}

/**
 * SpecViewerPanel — the same viewer inline, for the API hub's Spec tab: the
 * live document (opening in full mode), with a diff vs the previous revision
 * when `diffAgainst` is given.
 */
export function SpecViewerPanel({
	apiKey,
	diffAgainst,
}: {
	apiKey: ApiKey;
	diffAgainst?: SpecDiffBase | null;
}) {
	const title = 'Live OpenAPI spec';
	const view = useSpecView({ apiKey, active: true, diffAgainst, defaultMode: 'full' });
	return (
		<section
			className="bg-surface-1 rounded-lg [--field-bg:var(--surface-field)]"
			aria-label={title}
			data-testid="spec-viewer-panel"
		>
			<div className="flex flex-wrap items-center justify-between gap-2 px-5 pt-4 pb-1">
				<h2 className="font-heading text-foreground-name font-semibold">{title}</h2>
				<div className="flex items-center gap-2">
					<SpecActions view={view} />
				</div>
			</div>
			<div className="px-5 pt-2 pb-5">
				<SpecViewBody
					apiKey={apiKey}
					view={view}
					diffAgainst={diffAgainst}
					idPrefix="spec-panel"
					testIds={PANEL_TEST_IDS}
					maxHeightClass="max-h-[70vh]"
				/>
			</div>
		</section>
	);
}
