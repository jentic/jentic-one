/**
 * The workspace panel's "Needs attention" block — a quiet list under a small
 * caps label, no tinted box: each item is a tone-coloured icon, a headline
 * ("2 APIs · failed calls in the last 7 days") and a faint line of the API
 * names. Every name stays actionable — a link to its hub, or (for "no
 * credential", when the host offers it) a button opening Add credential in
 * place; "+N more" expands the names in place.
 *
 * Collapsed, it keeps the first two items (the digest's `ATTENTION_ORDER`:
 * most impact on agents first) and a quiet "Show all (N)" text button under
 * them, which turns into "Show less" once expanded.
 *
 * With ≤ 2 items there is nothing to collapse: no toggle, and every item
 * shows whatever the stored preference says (it is neither read nor
 * overwritten). With more, collapsed is the default; once the user toggles,
 * the choice is stored (`library.attention.expanded`) and wins over that
 * default on later visits.
 */
import { useId, useState } from 'react';
import { AlertTriangle, FileClock, GitPullRequestArrow, KeyRound, RefreshCw } from 'lucide-react';
import { AppLink, Button, SectionLabel } from '@/shared/ui';
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
	credentials: 'text-warning',
	drafts: 'text-foreground-faint',
};

/**
 * An API name in an item's detail line: quiet (inherits the faint tier) until
 * hovered; the keyboard focus ring comes from `Button` / `AppLink`.
 */
const NAME_CLASS =
	'min-w-0 truncate rounded-sm text-xs text-inherit hover:bg-transparent hover:text-foreground hover:underline active:scale-100';

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
		<li
			className="grid grid-cols-[16px_minmax(0,1fr)] items-start gap-2.5 py-1.5"
			data-testid={`attention-${entry.id}`}
		>
			<Icon
				className={`mt-0.5 h-[15px] w-[15px] shrink-0 ${ATTENTION_TONE[entry.id]}`}
				aria-hidden="true"
			/>
			<div className="min-w-0">
				<p className="text-foreground-lighter text-[13.5px] leading-snug font-semibold">
					{count}
					{entry.atLeast ? '+' : ''} {noun} · {entry.label}
				</p>
				<p
					id={listId}
					className="text-foreground-faint mt-px flex flex-wrap items-baseline gap-x-1.5 gap-y-0.5 text-xs"
				>
					{shown.map((row, i) => (
						<span
							key={row.key}
							className="inline-flex min-w-0 items-baseline gap-x-1.5"
						>
							{i > 0 && <span aria-hidden="true">·</span>}
							{entry.id === 'credentials' && onAddCredential ? (
								<Button
									variant="ghost"
									size="sm"
									onClick={() => onAddCredential(row)}
									aria-label={`Add a credential for ${row.title}`}
									title={`Add a credential for ${row.title}`}
									className={cn(NAME_CLASS, 'h-auto p-0 font-normal')}
									data-testid="attention-add-credential"
								>
									{row.title}
								</Button>
							) : (
								<AppLink href={hrefFor(row, entry)} className={NAME_CLASS}>
									{row.title}
									{entry.id === 'failures' && row.usage
										? ` (${row.usage.failed})`
										: ''}
								</AppLink>
							)}
						</span>
					))}
					{rest > 0 && (
						<Button
							variant="ghost"
							size="sm"
							onClick={() => setExpanded((v) => !v)}
							aria-expanded={expanded}
							aria-controls={listId}
							className={cn(
								NAME_CLASS,
								'text-foreground-sub h-auto p-0 font-semibold',
							)}
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

	return (
		<div data-testid="workspace-panel-attention" data-tone="warning" data-expanded={expanded}>
			<SectionLabel as="h3" className="mb-1.5">
				Needs attention · {attention.length}
			</SectionLabel>

			<ul id={listId}>
				{shown.map((entry) => (
					<AttentionItem key={entry.id} entry={entry} onAddCredential={onAddCredential} />
				))}
			</ul>

			{collapsible && (
				<Button
					variant="ghost"
					size="sm"
					onClick={() => setExpanded(!expanded)}
					aria-expanded={expanded}
					aria-controls={listId}
					className="text-foreground-sub hover:text-foreground h-auto rounded-sm px-0 pt-1 pb-0 pl-6 text-xs font-semibold hover:bg-transparent active:scale-100"
					data-testid="workspace-panel-attention-toggle"
				>
					{expanded ? 'Show less' : `Show all (${attention.length})`}
				</Button>
			)}
		</div>
	);
}
