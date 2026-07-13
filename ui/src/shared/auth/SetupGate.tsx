import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { getHealth, HEALTH_QUERY_KEY } from '@/shared/api';
import { ROUTES } from '@/shared/app/routes';

/**
 * Pre-session routing gate for the no-credential first run.
 *
 * Wraps the unauthenticated entry points (`/login`, `/setup`) and steers between
 * them based on the server's `setup_required` health flag:
 *   - setup_required && not on /setup  → /setup   (no account exists yet)
 *   - !setup_required && on /setup     → /login   (setup already done)
 *
 * While health is in flight we render nothing to avoid flashing the wrong form.
 * If the health probe fails we fall through to the requested route rather than
 * trapping the operator — login/setup will surface a real error on submit.
 */
export function SetupGate() {
	const location = useLocation();
	const healthQuery = useQuery({
		queryKey: HEALTH_QUERY_KEY,
		queryFn: getHealth,
		staleTime: 30_000,
		retry: false,
	});

	if (healthQuery.isLoading) {
		return null;
	}

	const setupRequired = healthQuery.data?.setup_required ?? false;
	const onSetup = location.pathname === ROUTES.setup;

	if (setupRequired && !onSetup) {
		return <Navigate to={ROUTES.setup} replace />;
	}
	if (!setupRequired && onSetup) {
		return <Navigate to={ROUTES.login} replace />;
	}

	return <Outlet />;
}
