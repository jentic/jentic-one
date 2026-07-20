/**
 * Internal navigation helpers for the LLM Proxy surface. Kept module-local so
 * the `/app` basename never appears as a literal in views (ESLint-enforced) —
 * these compose from the shared `ROUTES` registry.
 */
import { ROUTES } from '@/shared/app/routes';

export function sessionPath(sessionId: string): string {
	return `${ROUTES.llmProxy}/${sessionId}`;
}
