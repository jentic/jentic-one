/* eslint-disable no-restricted-syntax */
import { Workflow } from 'lucide-react';
import { AppLink } from './AppLink';

interface WorkflowRowProps {
	name: string;
	description?: string;
	stepsCount?: number | null;
	/** When set, renders as a link. */
	href?: string;
	/** When set (and no href), renders as a clickable button. */
	onClick?: () => void;
}

/**
 * Shared workflow row used in the API detail sheet, workspace API detail page,
 * and anywhere a compact workflow entry needs rendering.
 */
export function WorkflowRow({ name, description, stepsCount, href, onClick }: WorkflowRowProps) {
	const content = (
		<>
			<Workflow className="text-accent-teal mt-0.5 h-4 w-4 shrink-0" />
			<div className="min-w-0 flex-1">
				<p className="text-foreground truncate text-sm font-medium">{name}</p>
				{description && description !== name && (
					<p className="text-muted-foreground mt-0.5 line-clamp-2 text-xs leading-relaxed">
						{description}
					</p>
				)}
				{stepsCount != null && stepsCount > 0 && (
					<p className="text-muted-foreground/80 mt-0.5 text-[11px]">
						{stepsCount} step{stepsCount === 1 ? '' : 's'}
					</p>
				)}
			</div>
		</>
	);

	const cls =
		'hover:bg-muted/50 flex w-full items-start gap-3 rounded-md px-2 py-2 text-left transition-colors';

	if (href) {
		return (
			<AppLink href={href} className={cls}>
				{content}
			</AppLink>
		);
	}

	return (
		<button type="button" onClick={onClick} className={cls}>
			{content}
		</button>
	);
}
