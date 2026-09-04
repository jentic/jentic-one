/**
 * InitialScopesField — optional "start with these scopes" section for the
 * create sheets (POST /agents and /service-accounts
 * already accept `scopes[]`, so a new actor shouldn't need a follow-up PUT
 * from its detail page just to get its first grants).
 *
 * Collapsed by default behind a disclosure — most creates don't grant scopes,
 * and the picker is tall. Selection state is lifted to the sheet so it can be
 * included in the POST body and reset on success. Non-grantable catalogue
 * entries are rendered disabled, mirroring the Edit-scopes dialog.
 */
import { useMemo, useState } from 'react';
import { ChevronDown } from 'lucide-react';
import { ErrorAlert, LoadingState, ScopePicker } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import { extractResourceFromScope } from '@/shared/lib';
import { usePermissionCatalogue } from '@/modules/agents/api';
import { catalogueToScopes } from '@/modules/agents/components/ScopesCard';

interface InitialScopesFieldProps {
	selected: string[];
	onChange: (scopes: string[]) => void;
	/** Unique id prefix so two sheets never collide on aria wiring. */
	idPrefix: string;
}

export function InitialScopesField({ selected, onChange, idPrefix }: InitialScopesFieldProps) {
	const [open, setOpen] = useState(false);
	// Only fetch the catalogue once the section is opened — keeps the common
	// "create without scopes" path free of an extra request.
	const catalogue = usePermissionCatalogue({ enabled: open });

	const entries = useMemo(() => catalogue.data ?? [], [catalogue.data]);
	const scopes = useMemo(() => catalogueToScopes(entries), [entries]);
	const disabledScopes = useMemo(
		() => entries.filter((p) => !p.grantableByCaller).map((p) => p.name),
		[entries],
	);
	const disabledSet = useMemo(() => new Set(disabledScopes), [disabledScopes]);

	const toggle = (scope: string): void => {
		if (disabledSet.has(scope)) return;
		onChange(
			selected.includes(scope) ? selected.filter((s) => s !== scope) : [...selected, scope],
		);
	};
	const selectAll = (group?: string): void => {
		const pool = scopes.filter(
			(s) =>
				!disabledSet.has(s.scope) &&
				(!group || extractResourceFromScope(s.scope) === group),
		);
		onChange(Array.from(new Set([...selected, ...pool.map((s) => s.scope)])));
	};
	const deselectAll = (group?: string): void => {
		onChange(group ? selected.filter((s) => extractResourceFromScope(s) !== group) : []);
	};

	const bodyId = `${idPrefix}-initial-scopes`;

	return (
		<div className="border-border/60 rounded-lg border">
			<button
				type="button"
				className="text-foreground hover:bg-accent/40 flex w-full items-center justify-between gap-2 rounded-lg px-3 py-2.5 text-left text-sm font-medium transition-colors"
				aria-expanded={open}
				// Only reference the body while it's mounted — a dangling
				// aria-controls id is an a11y smell when collapsed.
				aria-controls={open ? bodyId : undefined}
				onClick={() => setOpen((v) => !v)}
			>
				<span>
					Initial scopes{' '}
					<span className="text-muted-foreground font-normal">
						{selected.length > 0 ? `· ${selected.length} selected` : '(optional)'}
					</span>
				</span>
				<ChevronDown
					className={cn(
						'text-muted-foreground h-4 w-4 shrink-0 transition-transform',
						open && 'rotate-180',
					)}
					aria-hidden
				/>
			</button>
			{open && (
				<div id={bodyId} className="border-border/60 border-t p-3">
					{catalogue.isPending ? (
						<LoadingState size="sm" message="Loading permissions…" />
					) : catalogue.error ? (
						<ErrorAlert message={catalogue.error as Error} />
					) : (
						<ScopePicker
							scopes={scopes}
							selectedScopes={selected}
							disabledScopes={disabledScopes}
							showRecommended={false}
							onScopeToggle={toggle}
							onSelectAll={selectAll}
							onDeselectAll={deselectAll}
						/>
					)}
				</div>
			)}
		</div>
	);
}
