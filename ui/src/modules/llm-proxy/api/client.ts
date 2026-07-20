/**
 * LLM Proxy · Sessions repository tier.
 *
 * The ONLY file in this module that touches `@/shared/api`. It issues the
 * requests through the shared low-level `apiRequest` primitive (behind the
 * Bearer-JWT `OpenAPI` config), so the mock MSW endpoints and the future
 * backend are reached identically. Errors are normalised to a module-owned
 * sentinel the service/view tiers can branch on.
 *
 * No backend serves `/proxy/*` yet (see the plan doc — real backend is future
 * scope). So when the live request fails because the route is absent (404) or
 * unreachable (network error, no MSW), we transparently fall back to the
 * bundled demo dataset via `lib/mockData`. Once the backend lands, that
 * fallback simply never triggers — delete the JSON + `lib/mockData` to remove it.
 */
import { ApiError, OpenAPI, apiRequest } from '@/shared/api';
import type { SessionBundle } from '@/modules/llm-proxy/api/types';
import { bundleForLocal, listSessionsLocal } from '@/modules/llm-proxy/lib/mockData';
import type { SessionListResponse } from '@/modules/llm-proxy/lib/mockData';

export type { SessionListResponse };

/**
 * True when a failed request should fall back to the bundled demo data:
 * the `/proxy/*` route doesn't exist on the target (404) or the target is
 * unreachable (network error / no MSW → `status` is null).
 */
function shouldUseLocalFallback(error: unknown): boolean {
	if (error instanceof ApiError) return error.status === 404;
	return true;
}

export class LlmProxyApiError extends Error {
	readonly status: number | null;
	readonly cause?: unknown;
	constructor(message: string, status: number | null, cause?: unknown) {
		super(message);
		this.name = 'LlmProxyApiError';
		this.status = status;
		this.cause = cause;
	}
}

function toLlmProxyError(error: unknown, fallback: string): LlmProxyApiError {
	if (error instanceof ApiError) {
		const detail = (error.body as { detail?: string } | undefined)?.detail ?? error.message;
		return new LlmProxyApiError(detail || fallback, error.status, error);
	}
	if (error instanceof Error) {
		return new LlmProxyApiError(error.message || fallback, null, error);
	}
	return new LlmProxyApiError(fallback, null, error);
}

export async function listSessions(): Promise<SessionListResponse> {
	try {
		return await apiRequest<SessionListResponse>(OpenAPI, {
			method: 'GET',
			url: '/proxy/sessions',
		});
	} catch (error) {
		if (shouldUseLocalFallback(error)) return listSessionsLocal();
		throw toLlmProxyError(error, 'Failed to load sessions.');
	}
}

export async function getSession(id: string): Promise<SessionBundle> {
	try {
		return await apiRequest<SessionBundle>(OpenAPI, {
			method: 'GET',
			url: '/proxy/sessions/{id}',
			path: { id },
		});
	} catch (error) {
		if (shouldUseLocalFallback(error)) {
			const local = bundleForLocal(id);
			if (local) return local;
		}
		throw toLlmProxyError(error, 'Failed to load session.');
	}
}
