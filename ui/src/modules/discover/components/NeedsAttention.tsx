/**
 * The workspace panel's "Needs attention" block — collapsible, like "Recent
 * changes". Expanded it is the full list (headline per kind, the API names,
 * "+N more" in place, a "no credential" name opening Add credential in place).
 *
 * Collapsed, it keeps the first two items (the digest's `ATTENTION_ORDER`: most impact on agents first) in full and
 * offers "Show all (N)".
 *
 * With ≤ 2 items there is nothing to collapse: the header is a plain heading
 * (no toggle) and every item shows, whatever the stored preference says (it
 * is neither read nor overwritten). With more, the header is the toggle —
 * collapsed by default; once the user toggles, the choice is stored
 * (`library.attention.expanded`) and wins over that default on later visits.
 */
import { useId, useState } from 'react';
import {
	AlertTriangle,
	ChevronDown,
	FileClock,
	GitPullRequestArrow,
	KeyRound,
	RefreshCw,
} from 'lucide-react';
import { AppLink, Button } from '@/shared/ui';
import { readBool, writeBool } from '@/shared/app/rail/railPreferences';
import { ROUTE_PATHS } from '@/shared/app/routes';
import { cn } from '@/shared/lib/utils';
import type { AttentionEntry, AttentionId, WorkspaceDigestRow } from '@/modules/discover/api';

/** localStorage key for the user's explicit expand / collapse choice. */
export const ATTENTION_EXPANDED_KEY = 'library.attention.expanded';

/** Items kept visible (in full) while collapsed; at most this many ⇒ not collapsible. */
const TOP_N = 2;

const ATTENTION_ICON: Record<AttentionId, typeof AlertTriangle> = {
	updates: RefreshCw,
	overlays: GitPullRequestArrow,
	failures: AlertTriangle,
	credentials: KeyRound,
	drafts: FileClock,
};

const ATTENTION_TONE: Record<AttentionId, string> = {
	updates: 'text-warning',
	overlays: 'text-primary',
	failures: 'text-danger',
	credentials: 'text-accent-orange',
	drafts: 'text-muted-foreground',
};

/** Names an attention item lists before "+N more" (which expands the rest in place). */
const ATTENTION_PREVIEW = 3;

function hrefFor(row: WorkspaceDigestRow, entry: AttentionEntry): string {
	// Without an in-place flow, "No credential" still names one API per
	// link, each opening that API's hub straight onto its Add credential form.
	return ROUTE_PATHS.workspaceApiHub(row.ref, entry.tab, {
		addCredential: entry.id === 'credentials',
	});
}

function AttentionItem({
	entry,
	onAddCredential,
}: {
	entry: AttentionEntry;
	onAddCredential?: (row: WorkspaceDigestRow) => void;
}) {
	const [expanded, setExpanded] = useState(false);
	const listId = useId();
	const Icon = ATTENTION_ICON[entry.id];
	const count = entry.rows.length;
	// "+N more" expands the names in place; every name behaves like the first
	// ones (a "no credential" name still opens Add credential in place).
	const shown = expanded ? entry.rows : entry.rows.slice(0, ATTENTION_PREVIEW);
	const rest = count - ATTENTION_PREVIEW;
	const noun = count === 1 ? 'API' : 'APIs';
	return (
		<li className="flex items-start gap-2.5 px-1 py-1.5" data-testid={`attention-${entry.id}`}>
			<Icon
				className={`mt-0.5 h-4 w-4 shrink-0 ${ATTENTION_TONE[entry.id]}`}
				aria-hidden="true"
			/>
			<div className="min-w-0 flex-1">
				<p className="text-foreground text-sm">
					<strong className="font-semibold">
						{count}
						{entry.atLeast ? '+' : ''}
					</strong>{' '}
					{noun} · {entry.label}
				</p>
				<p id={listId} className="mt-0.5 flex flex-wrap gap-x-2 gap-y-0.5 text-xs">
					{shown.map((row) =>
						entry.id === 'credentials' && onAddCredential ? (
							<Button
								key={row.key}
								variant="ghost"
								size="sm"
								onClick={() => onAddCredential(row)}
								aria-label={`Add a credential for ${row.title}`}
								title={`Add a credential for ${row.title}`}
								className="text-primary hover:text-primary h-auto min-w-0 truncate rounded-sm p-0 text-xs font-normal hover:bg-transparent hover:underline active:scale-100"
								data-testid="attention-add-credential"
							>
								{row.title}
							</Button>
						) : (
							<AppLink
								key={row.key}
								href={hrefFor(row, entry)}
								className="text-primary truncate hover:underline"
							>
								{row.title}
								{entry.id === 'failures' && row.usage
									? ` (${row.usage.failed})`
									: ''}
							</AppLink>
						),
					)}
					{rest > 0 && (
						<Button
							variant="ghost"
							size="sm"
							onClick={() => setExpanded((v) => !v)}
							aria-expanded={expanded}
							aria-controls={listId}
							className="text-muted-foreground hover:text-foreground h-auto rounded-sm p-0 text-xs font-normal hover:bg-transparent active:scale-100"
							data-testid="attention-more"
						>
							{expanded ? 'Show fewer' : `+${rest} more`}
						</Button>
					)}
				</p>
			</div>
		</li>
	);
}

/** The stored choice, or null when the user never toggled (then the auto rule applies). */
function readStoredExpanded(): boolean | null {
	if (typeof window === 'undefined') return null;
	try {
		if (window.localStorage.getItem(ATTENTION_EXPANDED_KEY) === null) return null;
	} catch {
		return null;
	}
	// Same '1' / '0' encoding as the rail's stored preferences.
	return readBool(ATTENTION_EXPANDED_KEY, false);
}

export function NeedsAttention({
	attention,
	onAddCredential,
}: {
	/** Non-empty, in the digest's `ATTENTION_ORDER` (most impact on agents first). */
	attention: AttentionEntry[];
	onAddCredential?: (row: WorkspaceDigestRow) => void;
}) {
	const [stored, setStored] = useState<boolean | null>(readStoredExpanded);
	const collapsible = attention.length > TOP_N;
	const expanded = !collapsible || (stored ?? false);
	const listId = useId();

	function setExpanded(next: boolean) {
		setStored(next);
		writeBool(ATTENTION_EXPANDED_KEY, next);
	}

	const shown = expanded ? attention : attention.slice(0, TOP_N);
	const hiddenCount = attention.length - shown.length;

	return (
		<div
			className="border-warning/30 bg-warning/10 rounded-lg border px-2 pt-1.5 pb-1"
			data-testid="workspace-panel-attention"
			data-tone="warning"
			data-expanded={expanded}
		>
			{!collapsible ? (
				<h3 className="font-heading text-warning flex items-center gap-1.5 px-1 py-1 text-sm font-semibold">
					<AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />
					Needs attention{' '}
					<span className="text-xs font-normal">· {attention.length}</span>
				</h3>
			) : (
				<h3 className="font-heading text-sm font-semibold">
					<Button
						variant="ghost"
						size="sm"
						onClick={() => setExpanded(!expanded)}
						aria-expanded={expanded}
						aria-controls={listId}
						className="text-warning hover:text-warning hover:bg-warning/10 h-auto w-full justify-between rounded-md px-1 py-1 text-sm font-semibold active:scale-100"
						data-testid="workspace-panel-attention-toggle"
					>
						<span className="flex items-center gap-1.5">
							<AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />
							Needs attention{' '}
							<span className="text-xs font-normal">· {attention.length}</span>
						</span>
						<ChevronDown
							className={cn('h-4 w-4 transition-transform', expanded && 'rotate-180')}
							aria-hidden="true"
						/>
					</Button>
				</h3>
			)}

			<ul id={listId} className="divide-warning/15 divide-y">
				{shown.map((entry) => (
					<AttentionItem key={entry.id} entry={entry} onAddCredential={onAddCredential} />
				))}
			</ul>

			{hiddenCount > 0 && (
				<Button
					variant="ghost"
					size="sm"
					onClick={() => setExpanded(true)}
					aria-controls={listId}
					className="text-warning hover:text-warning hover:bg-warning/10 mb-0.5 h-7 w-full rounded-md text-xs font-medium active:scale-100"
					data-testid="attention-show-all"
				>
					Show all ({attention.length})
				</Button>
			)}
		</div>
	);
}
