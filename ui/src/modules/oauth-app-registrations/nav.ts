import { KeyRound } from 'lucide-react';
import type { NavItem } from '@/shared/app/nav';

/**
 * Optional nav entry for the OAuth App Registrations admin surface. Kept as
 * a named export the consumer can hand to `registerExtraNavItems` (the
 * downstream-build seam) rather than appended to `navItems` — the OSS
 * primary nav intentionally excludes admin surfaces (the shell separates
 * operator/admin areas via the `secondary` slot), so we don't add to the
 * built-in registry here.
 */
export const oauthAppRegistrationsNav: NavItem = {
	id: 'oauth-app-registrations',
	label: 'OAuth apps',
	to: '/admin/oauth-app-registrations',
	order: 80,
	icon: KeyRound,
	requiredPermission: 'org:admin',
	secondary: true,
};
