/**
 * ExpandAllToggle — the "Can call" list's one Expand all ⇄ Collapse all
 * button, beside the list⇄cards lens in the agent card's header.
 *
 * With no row pinned it reads "Expand all" and pins every row; with ANY row
 * pinned it reads "Collapse all" and lets every pin go — so a way to close
 * is always one click away. Both labels (and both icons) sit stacked in one
 * cell and crossfade, so the button keeps the longer label's width and
 * nothing beside it moves. It keeps its words at every width (no tooltip:
 * the label says it); its aria-label names the rows it acts on.
 */
import { ChevronsDownUp, ChevronsUpDown } from 'lucide-react';
import { useReducedMotionConfig } from 'framer-motion';
import { Button } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';

interface ExpandAllToggleProps {
	/** Whether any row is pinned open. */
	anyOpen: boolean;
	onExpandAll: () => void;
	onCollapseAll: () => void;
	/** DOM id of the list it opens and shuts (aria-controls). */
	controls: string;
}

const FADE = 'transition-opacity duration-150 ease-out';

export function ExpandAllToggle({
	anyOpen,
	onExpandAll,
	onCollapseAll,
	controls,
}: ExpandAllToggleProps) {
	const reduced = useReducedMotionConfig() ?? false;
	const label = anyOpen ? 'Collapse all rows' : 'Expand all rows';
	const fade = reduced ? 'transition-none' : FADE;
	/** One of the two stacked states: shown, or held for its width only. */
	const layer = (shown: boolean) => cn('col-start-1 row-start-1', fade, !shown && 'opacity-0');

	return (
		<Button
			variant="tonal"
			size="xs"
			data-testid="expand-all-rows"
			data-state={anyOpen ? 'collapse' : 'expand'}
			aria-label={label}
			aria-controls={controls}
			onClick={anyOpen ? onCollapseAll : onExpandAll}
			className="shrink-0"
		>
			<span aria-hidden="true" className="grid h-3.5 w-3.5 shrink-0">
				<ChevronsUpDown className={cn('h-3.5 w-3.5', layer(!anyOpen))} />
				<ChevronsDownUp className={cn('h-3.5 w-3.5', layer(anyOpen))} />
			</span>
			<span aria-hidden="true" className="grid">
				<span className={layer(!anyOpen)}>Expand all</span>
				<span className={layer(anyOpen)}>Collapse all</span>
			</span>
		</Button>
	);
}
