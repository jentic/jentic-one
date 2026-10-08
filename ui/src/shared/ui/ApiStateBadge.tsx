/**
 * ApiStateBadge — the one vocabulary for a workspace API's serving state, so
 * the Library catalog's docked panel, the workspace cards and the API hub all
 * read it the same way (mirrors `ActorStatusBadge`'s single-sourcing).
 *
 * Derived from real registry fields only:
 *   - `live`   — the API has a current (live) revision (`current_revision_id`)
 *   - `draft`  — no live revision yet (`current_revision_id === null`)
 *   - `update` — the upstream spec changed and the API hasn't adopted it
 *                (`update_available`, Flow-3); shown alongside live/draft
 */
import { Badge, type Variant } from '@/shared/ui/Badge';
import { cn } from '@/shared/lib/utils';

export type ApiServingState = 'live' | 'draft' | 'update';

/** Every state an API is in, primary first: `live`/`draft`, then `update` when flagged. */
export function apiServingState(api: {
	currentRevisionId: string | null;
	updateAvailable?: boolean | null;
}): { serving: 'live' | 'draft'; updateAvailable: boolean; states: ApiServingState[] } {
	const serving = api.currentRevisionId !== null ? 'live' : 'draft';
	const updateAvailable = api.updateAvailable === true;
	return {
		serving,
		updateAvailable,
		states: updateAvailable ? [serving, 'update'] : [serving],
	};
}

export const API_STATE_LABELS: Record<ApiServingState, string> = {
	live: 'Live',
	draft: 'Draft',
	update: 'Update available',
};

export const API_STATE_BADGE_VARIANT: Record<ApiServingState, Variant> = {
	live: 'success',
	// A draft is a neutral fact, not a warning (orange means "no credential").
	draft: 'neutral',
	update: 'warning',
};

/**
 * How the state is drawn: `pill` (a soft borderless pill with a dot — headers
 * like the API hub and the preview sheet) or `text` (the plain word in a meta
 * line; "Update available" alone takes the warning colour).
 */
export type ApiStateBadgeVariant = 'pill' | 'text';

const TEXT_TONE: Record<ApiServingState, string> = {
	live: 'text-foreground-sub',
	draft: 'text-foreground-sub',
	update: 'text-foreground-lighter font-semibold',
};

export function ApiStateBadge({
	state,
	className,
	short,
	testId,
	variant = 'pill',
}: {
	state: ApiServingState;
	className?: string;
	/** Compact label ("Update" instead of "Update available") for dense rows. */
	short?: boolean;
	/** Override the default `api-state-{state}` test id (keeps legacy hooks stable). */
	testId?: string;
	variant?: ApiStateBadgeVariant;
}) {
	const label = short && state === 'update' ? 'Update' : API_STATE_LABELS[state];
	const id = testId ?? `api-state-${state}`;
	if (variant === 'text') {
		return (
			<span data-testid={id} className={cn('text-xs', TEXT_TONE[state], className)}>
				{label}
			</span>
		);
	}
	return (
		<Badge variant={API_STATE_BADGE_VARIANT[state]} dot className={className} data-testid={id}>
			{label}
		</Badge>
	);
}

/**
 * An API's serving-state badges side by side (`Live`/`Draft`, plus `Update`
 * when the upstream spec changed) — the pair every surface shows.
 */
export function ApiStateBadges({
	currentRevisionId,
	updateAvailable,
	className,
	short,
	variant,
}: {
	currentRevisionId: string | null;
	updateAvailable?: boolean | null;
	/** Applied to each badge (e.g. the dense-row sizing). */
	className?: string;
	short?: boolean;
	variant?: ApiStateBadgeVariant;
}) {
	const { states } = apiServingState({ currentRevisionId, updateAvailable });
	return (
		<>
			{states.map((state) => (
				<ApiStateBadge
					key={state}
					state={state}
					className={className}
					short={short}
					variant={variant}
				/>
			))}
		</>
	);
}
