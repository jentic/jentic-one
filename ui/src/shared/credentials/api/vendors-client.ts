/**
 * Integrations repository tier — the ONLY place in the module that talks
 * HTTP. Views and hooks import this module, not `fetch` directly.
 *
 * The endpoints backing this module were added after the last OpenAPI
 * regeneration, so we hand-call them via `fetch` + `getToken` from
 * `@/shared/api`. Swap to generated services when `make openapi` runs.
 */

import { AgentsService, getToken, type AgentListResponse } from '@/shared/api';
import type {
	ConfirmRequest,
	ConfirmResponse,
	ConnectRequest,
	ConnectResponse,
	ReviewSession,
	StatusResponse,
	VendorAuthCapabilities,
	VendorListResponse,
} from '@/shared/credentials/api/vendors-types';

export class IntegrationsApiError extends Error {
	readonly status: number | null;
	readonly cause?: unknown;

	constructor(message: string, status: number | null, cause?: unknown) {
		super(message);
		this.name = 'IntegrationsApiError';
		this.status = status;
		this.cause = cause;
	}
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
	const token = await getToken();
	const headers = new Headers(init.headers);
	if (token) headers.set('Authorization', `Bearer ${token}`);
	if (init.body && !headers.has('Content-Type')) {
		headers.set('Content-Type', 'application/json');
	}
	headers.set('Accept', 'application/json');
	let response: Response;
	try {
		response = await fetch(path, { ...init, headers });
	} catch (err) {
		throw new IntegrationsApiError('network error', null, err);
	}
	if (!response.ok) {
		let detail: string | undefined;
		try {
			const body = await response.json();
			detail = typeof body?.detail === 'string' ? body.detail : undefined;
		} catch {
			// ignore parse failure — fall back to statusText
		}
		throw new IntegrationsApiError(
			detail ?? response.statusText ?? `HTTP ${response.status}`,
			response.status,
		);
	}
	if (response.status === 204) return undefined as T;
	return (await response.json()) as T;
}

export function startIntegrationConnect(body: ConnectRequest): Promise<ConnectResponse> {
	return request('/integrations:connect', {
		method: 'POST',
		body: JSON.stringify(body),
	});
}

export function getConnectSession(sessionId: string): Promise<ReviewSession> {
	return request(`/connect-sessions/${encodeURIComponent(sessionId)}`);
}

export function confirmConnectSession(
	sessionId: string,
	body: ConfirmRequest,
): Promise<ConfirmResponse> {
	return request(`/connect-sessions/${encodeURIComponent(sessionId)}:confirm`, {
		method: 'POST',
		body: JSON.stringify(body),
	});
}

export function pollConnectSessionStatus(
	sessionId: string,
	pollToken: string,
): Promise<StatusResponse> {
	const url = `/connect-sessions/${encodeURIComponent(sessionId)}/status?poll_token=${encodeURIComponent(pollToken)}`;
	return request(url);
}

/**
 * Cancel an in-flight connect session. Idempotent — the backend
 * ``:cancel`` route returns 204 even if the session is already gone,
 * so we can fire this from unmount cleanup without needing to check
 * whether the flow already finished.
 */
export function cancelConnectSession(sessionId: string, pollToken: string): Promise<void> {
	const url = `/connect-sessions/${encodeURIComponent(sessionId)}:cancel?poll_token=${encodeURIComponent(pollToken)}`;
	return request(url, { method: 'POST' });
}

/**
 * Fire-and-forget cancel via ``navigator.sendBeacon``. Used on tab-close
 * (``beforeunload``) where an in-flight ``fetch`` would be aborted by the
 * browser but ``sendBeacon`` is guaranteed to deliver. Returns whether the
 * browser accepted the beacon; callers should keep firing the regular
 * ``cancelConnectSession`` on in-page dismiss (dialog close, unmount) since
 * ``sendBeacon`` can't set custom headers or observe the response.
 */
export function cancelConnectSessionBeacon(sessionId: string, pollToken: string): boolean {
	if (typeof navigator === 'undefined' || typeof navigator.sendBeacon !== 'function') {
		return false;
	}
	const url = `/connect-sessions/${encodeURIComponent(sessionId)}:cancel?poll_token=${encodeURIComponent(pollToken)}`;
	// The body isn't used (poll_token rides on the query string); an empty
	// Blob keeps the browser's beacon path happy without conjuring a
	// Content-Type the backend has to ignore.
	return navigator.sendBeacon(url, new Blob([], { type: 'application/octet-stream' }));
}

export function listVendors(): Promise<VendorListResponse> {
	return request('/vendors');
}

export function getVendorAuthCapabilities(vendorKey: string): Promise<VendorAuthCapabilities> {
	return request(`/vendors/${encodeURIComponent(vendorKey)}/auth-capabilities`);
}

/**
 * Thin adapter around the generated AgentsService so views/hooks in this
 * module never touch `@/shared/api` directly.
 */
export async function listAgentsForPicker(): Promise<AgentListResponse> {
	return AgentsService.listAgents({ limit: 100 });
}
