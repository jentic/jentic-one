import { useCallback, useEffect, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { Check, ChevronRight, Minus } from 'lucide-react';
import { cn } from '@/shared/lib/utils';
import type { EnhancedScope, ScopeGroup as ScopeGroupType } from '@/shared/lib/scopes';

/**
 * Collapsible group of scopes for one resource (ported from jentic-webapp's
 * `ScopeGroup`). Header shows a tri-state select-all checkbox + a
 * `selected/total` count; the body lists individual scope rows with their
 * description and (optionally) a "Recommended" hint.
 *
 * Source-agnostic: drives credentials' OAuth2 scopes and the platform
 * permission scopes on actors. Pure presentation — selection state + toggles
 * are owned by the parent `ScopePicker`.
 */
export interface ScopeGroupProps {
	group: ScopeGroupType;
	selectedScopes: Set<string>;
	onToggleScope: (scope: string) => void;
	onSelectAll: () => void;
	onDeselectAll: () => void;
	/** Force-expanded while a search is active. */
	defaultExpanded?: boolean;
	/** Scopes the caller may not grant — rendered disabled, never toggled. */
	disabledScopes?: Set<string>;
	/** Show the per-scope "Recommended" badge (OAuth2 only). Default true. */
	showRecommended?: boolean;
}

export function ScopeGroup({
	group,
	selectedScopes,
	onToggleScope,
	onSelectAll,
	onDeselectAll,
	defaultExpanded = false,
	disabledScopes,
	showRecommended = true,
}: ScopeGroupProps) {
	const selectedCount = group.scopes.filter((s) => selectedScopes.has(s.scope)).length;
	const totalCount = group.scopes.length;
	// Only scopes the caller can actually grant count toward "all selected".
	const selectableCount = group.scopes.filter((s) => !disabledScopes?.has(s.scope)).length;
	const allSelected = selectedCount === selectableCount && selectableCount > 0;
	const someSelected = selectedCount > 0 && selectedCount < selectableCount;

	const [isExpanded, setIsExpanded] = useState(defaultExpanded);

	// Mirror the search state: expand to reveal matches, collapse when cleared.
	useEffect(() => {
		setIsExpanded(defaultExpanded);
	}, [defaultExpanded]);

	const toggleExpanded = useCallback(() => setIsExpanded((p) => !p), []);

	const handleSelectAllClick = useCallback(
		(e: React.MouseEvent) => {
			e.stopPropagation();
			if (allSelected) onDeselectAll();
			else onSelectAll();
		},
		[allSelected, onSelectAll, onDeselectAll],
	);

	return (
		<div className="border-border overflow-hidden rounded-xl border">
			{/*
			 * Header row is a non-interactive flex container holding two sibling
			 * controls — the expand toggle and the select-all checkbox — so neither
			 * interactive element nests inside the other (avoids axe
			 * `nested-interactive`).
			 */}
			<div className="bg-muted/30 hover:bg-muted/50 flex w-full items-center gap-3 px-3 py-2.5 transition-colors">
				<button
					type="button"
					aria-expanded={isExpanded}
					aria-label={`${group.name} scopes, ${selectedCount} of ${totalCount} selected`}
					onClick={toggleExpanded}
					className="flex flex-1 cursor-pointer items-center gap-3 text-left"
				>
					<motion.span
						animate={{ rotate: isExpanded ? 90 : 0 }}
						transition={{ duration: 0.2 }}
					>
						<ChevronRight className="text-muted-foreground h-4 w-4" />
					</motion.span>

					<span className="text-foreground flex-1 text-sm font-medium">{group.name}</span>

					<span
						className={cn(
							'rounded-full px-2 py-0.5 text-[11px] font-medium',
							selectedCount > 0
								? 'bg-primary/10 text-primary'
								: 'bg-muted text-muted-foreground',
						)}
					>
						{selectedCount}/{totalCount}
					</span>
				</button>

				<button
					type="button"
					role="checkbox"
					aria-checked={allSelected ? true : someSelected ? 'mixed' : false}
					disabled={selectableCount === 0}
					onClick={handleSelectAllClick}
					title={allSelected ? 'Deselect all' : 'Select all'}
					aria-label={
						allSelected ? `Deselect all ${group.name}` : `Select all ${group.name}`
					}
					className={cn(
						'flex h-5 w-5 shrink-0 items-center justify-center rounded border-2 transition-colors',
						selectableCount === 0 && 'cursor-not-allowed opacity-40',
						allSelected
							? 'border-primary bg-primary'
							: someSelected
								? 'border-primary bg-primary/50'
								: 'border-muted-foreground/30 hover:border-primary/50',
					)}
				>
					{allSelected && <Check className="text-primary-foreground h-3 w-3" />}
					{someSelected && !allSelected && (
						<Minus className="text-primary-foreground h-3 w-3" />
					)}
				</button>
			</div>

			<AnimatePresence initial={false}>
				{isExpanded && (
					<motion.div
						initial={{ height: 0, opacity: 0 }}
						animate={{ height: 'auto', opacity: 1 }}
						exit={{ height: 0, opacity: 0 }}
						transition={{ duration: 0.2 }}
						className="overflow-hidden"
					>
						<div className="divide-border/50 border-border divide-y border-t">
							{group.scopes.map((scope) => (
								<ScopeItem
									key={scope.scope}
									scope={scope}
									isSelected={selectedScopes.has(scope.scope)}
									disabled={disabledScopes?.has(scope.scope)}
									showRecommended={showRecommended}
									onToggle={(): void => onToggleScope(scope.scope)}
								/>
							))}
						</div>
					</motion.div>
				)}
			</AnimatePresence>
		</div>
	);
}

function ScopeItem({
	scope,
	isSelected,
	disabled,
	showRecommended,
	onToggle,
}: {
	scope: EnhancedScope;
	isSelected: boolean;
	disabled?: boolean;
	showRecommended: boolean;
	onToggle: () => void;
}) {
	return (
		<button
			type="button"
			role="checkbox"
			aria-checked={isSelected}
			aria-label={scope.scope}
			disabled={disabled}
			title={disabled ? 'You do not have permission to grant this scope' : undefined}
			onClick={onToggle}
			className={cn(
				'group flex w-full items-center gap-3 px-3 py-2.5 text-left transition-colors',
				disabled && 'cursor-not-allowed opacity-50',
				isSelected ? 'bg-primary/5 hover:bg-primary/10' : 'hover:bg-muted/50',
			)}
		>
			<span className="min-w-0 flex-1">
				<span className="flex items-center gap-2">
					<code className="text-foreground truncate text-xs font-medium">
						{scope.scope}
					</code>
					{showRecommended && scope.isRecommended && (
						<span className="text-success bg-success/10 shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium">
							Recommended
						</span>
					)}
				</span>
				{scope.description && (
					<span className="text-muted-foreground mt-0.5 line-clamp-2 block text-[11px] leading-snug">
						{scope.description}
					</span>
				)}
			</span>
			<span
				className={cn(
					'flex h-4 w-4 shrink-0 items-center justify-center rounded border-2 transition-colors',
					isSelected
						? 'border-primary bg-primary'
						: 'border-muted-foreground/30 group-hover:border-primary/50',
				)}
				aria-hidden
			>
				{isSelected && <Check className="text-primary-foreground h-2.5 w-2.5" />}
			</span>
		</button>
	);
}
