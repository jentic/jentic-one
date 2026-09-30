/**
 * How much a requested scope can do, for the moment an operator approves an
 * agent. A self-registering agent names its own scopes, and approval makes them
 * live (bounded only by the approver's own ceiling), so the ones that can
 * change data or administer the organisation are flagged before the click.
 */
export type ScopeRisk = 'admin' | 'write';

/** `admin` for any scope with an `admin` segment (`org:admin`), `write` for a
 * `…:write` scope, otherwise `null`. */
export function scopeRisk(scope: string): ScopeRisk | null {
	const segments = scope.trim().toLowerCase().split(':');
	if (segments.includes('admin')) return 'admin';
	if (segments[segments.length - 1] === 'write') return 'write';
	return null;
}
