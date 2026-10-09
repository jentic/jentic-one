import { ChevronLeft } from 'lucide-react';
import { Link, useLocation, useNavigate, type Location } from 'react-router';
import { cn } from '@/shared/lib/utils';

interface BackButtonProps {
	/** Static fallback destination when there's no in-app history to pop. */
	to: string;
	label: string;
	className?: string;
	/**
	 * When true, uses `navigate(-1)` (browser back) instead of a static link.
	 * Falls back to `to` if there's no in-app entry to pop (e.g. direct URL
	 * access or a new tab). Default: true.
	 */
	useHistory?: boolean;
	/** Override the default `back-button` test id. */
	testId?: string;
}

/**
 * Whether stepping back stays inside the app.
 *
 * `<BrowserRouter>` stamps each history entry it owns with an `idx` (0 on the
 * tab's first app entry), and keeps it across `replace` navigations — so a
 * direct visit followed by replaced tab switches still reads 0. Routers that
 * don't touch `window.history` (the tests' `MemoryRouter`) have no `idx`;
 * there the initial entry's `default` location key marks "nothing behind".
 */
function hasInAppHistory(location: Pick<Location, 'key'>): boolean {
	const idx: unknown = window.history.state?.idx;
	if (typeof idx === 'number') return idx > 0;
	return location.key !== 'default';
}

/**
 * Quiet "back to <parent>" link for detail pages.
 *
 * By default uses browser history (`navigate(-1)`) so the user returns
 * to wherever they came from. Falls back to the static `to` path when
 * opened via direct URL.
 */
export function BackButton({
	to,
	label,
	className,
	useHistory = true,
	testId = 'back-button',
}: BackButtonProps) {
	const navigate = useNavigate();
	const location = useLocation();

	const cls = cn(
		'text-muted-foreground hover:text-foreground inline-flex w-fit items-center gap-0.5 text-xs font-medium transition-colors',
		className,
	);

	if (useHistory) {
		return (
			<button
				type="button"
				onClick={() => {
					if (hasInAppHistory(location)) {
						navigate(-1);
					} else {
						navigate(to, { replace: true });
					}
				}}
				className={cls}
				data-testid={testId}
			>
				<ChevronLeft className="h-3.5 w-3.5" aria-hidden="true" />
				{label}
			</button>
		);
	}

	return (
		<Link to={to} className={cls} data-testid={testId}>
			<ChevronLeft className="h-3.5 w-3.5" aria-hidden="true" />
			{label}
		</Link>
	);
}
