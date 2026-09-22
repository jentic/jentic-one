import { useMemo, useState } from 'react';
import { Search, X } from 'lucide-react';
import { Input, Label } from '@/shared/ui';
import { ScopeGroup } from '@/shared/ui/ScopeGroup';
import {
	filterScopeGroups,
	groupScopesByResource,
	VOCABULARY_NOUNS,
	type EnhancedScope,
	type ScopeVocabulary,
} from '@/shared/lib/scopes';

/**
 * Grouped, searchable scope picker — a faithful port of jentic-webapp's
 * `ScopeSelector`: scopes are grouped by resource into collapsible
 * {@link ScopeGroup}s, with a search box, a global select/deselect-all toggle,
 * a live `selected/total` count, and per-group select-all.
 *
 * Source-agnostic. The credentials module feeds it OAuth2 provider scopes (with
 * "Recommended" badges + auto-selection); the agents surface
 * feeds it platform permissions (`showRecommended={false}`, with
 * `disabledScopes` for permissions the caller can't grant). Selection state
 * lives in the parent; this component operates purely on item names + the
 * callbacks.
 *
 * Because the two sources are different vocabularies — OAuth2 *scopes* on the
 * wire vs. internal-authorization *permissions* — the caller picks the noun the
 * chrome uses via {@link ScopePickerProps.vocabulary}, which is forwarded to
 * every {@link ScopeGroup} so the group headers announce the same noun.
 */

export interface ScopePickerProps {
	scopes: EnhancedScope[];
	selectedScopes: string[];
	onScopeToggle: (scope: string) => void;
	/** No `group` → select every scope; with `group` → that resource group only. */
	onSelectAll: (group?: string) => void;
	/** No `group` → clear every scope; with `group` → that resource group only. */
	onDeselectAll: (group?: string) => void;
	/** Scopes the caller may not grant — rendered disabled, never toggled. */
	disabledScopes?: string[];
	/** Show per-scope "Recommended" badges (OAuth2 only). Default true. */
	showRecommended?: boolean;
	/** Noun for the heading, search box and empty state. Default `'scope'`. */
	vocabulary?: ScopeVocabulary;
}

export function ScopePicker({
	scopes,
	selectedScopes,
	onScopeToggle,
	onSelectAll,
	onDeselectAll,
	disabledScopes,
	showRecommended = true,
	vocabulary = 'scope',
}: ScopePickerProps) {
	const noun = VOCABULARY_NOUNS[vocabulary];
	const [query, setQuery] = useState('');
	const selectedSet = useMemo(() => new Set(selectedScopes), [selectedScopes]);
	const disabledSet = useMemo(() => new Set(disabledScopes ?? []), [disabledScopes]);

	// Stable alphabetical grouping; doesn't reshuffle as the user selects.
	const groups = useMemo(() => groupScopesByResource(scopes), [scopes]);
	const filteredGroups = useMemo(() => filterScopeGroups(groups, query), [groups, query]);

	const selectableTotal = useMemo(
		() => scopes.filter((s) => !disabledSet.has(s.scope)).length,
		[scopes, disabledSet],
	);
	const selectedCount = selectedScopes.length;
	const allSelected = selectedCount === selectableTotal && selectableTotal > 0;

	return (
		<div className="space-y-3">
			<div className="flex items-center justify-between">
				<div>
					<Label>{noun.heading}</Label>
					<p className="text-muted-foreground mt-0.5 text-xs">
						{selectedCount} of {selectableTotal} selected
					</p>
				</div>
				<button
					type="button"
					disabled={selectableTotal === 0}
					onClick={(): void => (allSelected ? onDeselectAll() : onSelectAll())}
					className="text-primary hover:text-primary/80 text-xs font-medium transition-colors disabled:opacity-40"
				>
					{allSelected ? 'Deselect all' : 'Select all'}
				</button>
			</div>

			<div className="relative">
				<Input
					value={query}
					onChange={(e): void => setQuery(e.target.value)}
					placeholder={`Search ${noun.plural}…`}
					aria-label={`Search ${noun.plural}`}
					startIcon={<Search className="h-3.5 w-3.5" />}
					className={query ? 'pr-9' : undefined}
				/>
				{query && (
					<button
						type="button"
						onClick={(): void => setQuery('')}
						className="text-muted-foreground hover:text-foreground absolute top-1/2 right-3 -translate-y-1/2"
						aria-label="Clear search"
					>
						<X className="h-3.5 w-3.5" />
					</button>
				)}
			</div>

			<div className="space-y-2.5">
				{filteredGroups.length === 0 ? (
					<div className="border-border bg-muted/30 rounded-xl border p-6 text-center">
						<p className="text-muted-foreground text-xs">
							{scopes.length === 0
								? `No ${noun.plural} available.`
								: `No ${noun.plural} match “${query}”`}
						</p>
					</div>
				) : (
					filteredGroups.map((group) => (
						<ScopeGroup
							key={group.id}
							group={group}
							selectedScopes={selectedSet}
							disabledScopes={disabledSet}
							showRecommended={showRecommended}
							vocabulary={vocabulary}
							onToggleScope={onScopeToggle}
							onSelectAll={(): void => onSelectAll(group.id)}
							onDeselectAll={(): void => onDeselectAll(group.id)}
							defaultExpanded={!!query}
						/>
					))
				)}
			</div>
		</div>
	);
}
