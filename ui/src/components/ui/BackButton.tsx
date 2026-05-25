import { ChevronLeft } from 'lucide-react';
import { Link } from 'react-router-dom';
import { cn } from '@/lib/utils';

interface BackButtonProps {
	to: string;
	label: string;
	className?: string;
}

/**
 * Quiet "back to <parent>" link for detail pages. Sized to read as a
 * trail affordance, not a body link — render it directly under
 * `<PageHeader>` (see `WorkflowDetailPage`, `ToolkitDetailPage`).
 */
export function BackButton({ to, label, className }: BackButtonProps) {
	return (
		<Link
			to={to}
			className={cn(
				'text-muted-foreground hover:text-foreground inline-flex w-fit items-center gap-0.5 text-xs font-medium transition-colors',
				className,
			)}
			data-testid="back-button"
		>
			<ChevronLeft className="h-3.5 w-3.5" aria-hidden="true" />
			{label}
		</Link>
	);
}
