import { OpenAPI } from '@/shared/api/generated/core/OpenAPI';
import { request as __request } from '@/shared/api/generated/core/request';
import type { HealthResponse } from '@/shared/api/generated/models/HealthResponse';
import '@/shared/api/client';
import { getAppConfig } from '@/shared/config';

export type Health = HealthResponse;

/**
 * Shared react-query key for the first-run health/setup probe. Centralised so
 * the SetupGate (reader) and the create-admin flow (invalidator) can never
 * drift — a mismatched literal would silently no-op the invalidation and strand
 * a freshly-created admin on the stale `setup_required: true` value.
 */
export const HEALTH_QUERY_KEY = ['health', 'setup'] as const;

/**
 * Health is read from a deploy-mode-dependent path (`/admin/health` combined vs
 * `/health` standalone), which the server tells the SPA via app config. The
 * generated `SystemService.health` hardcodes a single path, so we issue the
 * request through the generated core with the configured path to stay
 * mode-correct while still flowing through the shared auth/credentials config
 * (importing `@/shared/api/client` ensures it is applied).
 */
export function getHealth(): Promise<HealthResponse> {
	return __request<HealthResponse>(OpenAPI, {
		method: 'GET',
		url: getAppConfig().healthPath,
	});
}
