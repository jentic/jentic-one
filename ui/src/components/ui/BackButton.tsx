/* eslint-disable no-restricted-syntax */
import { ChevronLeft } from 'lucide-react';
import { Link, useNavigate } from 'react-router-dom';
import { cn } from '@/lib/utils';

interface BackButtonProps {
	/** Static fallback destination when there's no browser history to pop. */
	to: string;
	label: string;
	className?: string;
	/**
	 * When true, uses `navigate(-1)` (browser back) instead of a static link.
	 * Falls back to `to` if there's no history entry to pop (e.g. direct URL access).
	 * Default: true — most detail pages benefit from history-aware navigation.
	 */
	useHistory?: boolean;
}

/**
 * Quiet "back to <parent>" link for detail pages. Sized to read as a
 * trail affordance, not a body link — render it directly above the
 * page title (see `WorkflowDetailPage`, `ApiDetailPage`).
 *
 * By default uses browser history (`navigate(-1)`) so the user returns
 * to wherever they came from (e.g. API detail → workflow → back = API detail).
 * Falls back to the static `to` path when opened via direct URL.
 */
export function BackButton({ to, label, className, useHistory = true }: BackButtonProps) {
	const navigate = useNavigate();

	const cls = cn(
		'text-muted-foreground hover:text-foreground inline-flex w-fit items-center gap-0.5 text-xs font-medium transition-colors',
		className,
	);

	if (useHistory) {
		return (
			<button
				type="button"
				onClick={() => {
					if (window.history.state?.idx > 0) {
						navigate(-1);
					} else {
						navigate(to, { replace: true });
					}
				}}
				className={cls}
				data-testid="back-button"
			>
				<ChevronLeft className="h-3.5 w-3.5" aria-hidden="true" />
				{label}
			</button>
		);
	}

	return (
		<Link to={to} className={cls} data-testid="back-button">
			<ChevronLeft className="h-3.5 w-3.5" aria-hidden="true" />
			{label}
		</Link>
	);
}
