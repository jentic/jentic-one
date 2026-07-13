import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { useAuth } from '@/shared/auth/AuthContext';
import { ROUTES } from '@/shared/app/routes';

/**
 * Gates authenticated routes:
 *   - while the session is resolving → render nothing (avoids a login flash)
 *   - unauthenticated → redirect to /login (remembering where they were headed)
 *   - authenticated but must_change_password → force the change-password screen
 *
 * Mounted as a layout route (renders <Outlet/> for its children).
 */
export function AuthGuard() {
	const { status, mustChangePassword } = useAuth();
	const location = useLocation();

	if (status === 'loading') {
		return null;
	}

	if (status === 'unauthenticated') {
		return <Navigate to={ROUTES.login} replace state={{ from: location }} />;
	}

	if (mustChangePassword && location.pathname !== ROUTES.changePassword) {
		return <Navigate to={ROUTES.changePassword} replace />;
	}

	return <Outlet />;
}
