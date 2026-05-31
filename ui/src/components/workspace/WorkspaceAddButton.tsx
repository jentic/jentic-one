import { Plus } from 'lucide-react';
import type { ImportTab } from './ImportSourceDialog';
import { Button } from '@/components/ui/Button';

/**
 * "Add" entry point for the Workspace page header.
 *
 * Single primary button — no dropdown. The kind pivot (API vs
 * Workflow) lives inside `<ImportSourceDialog>` itself, which keeps
 * the trigger lightweight and removes a click for users who already
 * know what they're importing. Empty-state CTAs in the page body
 * still call into the same handler with the appropriate tab
 * pre-selected.
 *
 * `onSelect` always receives `'api'` from this trigger; we keep the
 * callback shape compatible with the empty-state CTAs so the page
 * has one entry point.
 */
export interface WorkspaceAddButtonProps {
	onSelect: (tab: ImportTab) => void;
}

export function WorkspaceAddButton({ onSelect }: WorkspaceAddButtonProps) {
	return (
		<Button
			variant="primary"
			size="sm"
			onClick={() => onSelect('api')}
			data-testid="workspace-add-button"
		>
			<Plus size={14} aria-hidden="true" />
			Add
		</Button>
	);
}
