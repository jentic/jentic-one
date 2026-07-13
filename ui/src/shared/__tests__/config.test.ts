import { describe, expect, it, afterEach, vi } from 'vitest';
import { getAppConfig, loadAppConfig } from '@/shared/config';

afterEach(() => {
	delete window.__APP_CONFIG__;
	vi.restoreAllMocks();
	vi.unstubAllGlobals();
});

describe('getAppConfig', () => {
	it('falls back to the combined-mode health path when no config is loaded', () => {
		expect(getAppConfig().healthPath).toBe('/admin/health');
	});

	it('uses the backend-loaded health path (e.g. standalone /health)', () => {
		window.__APP_CONFIG__ = { healthPath: '/health' };
		expect(getAppConfig().healthPath).toBe('/health');
	});
});

describe('loadAppConfig', () => {
	it('fetches /app-config.json and caches the health path for getAppConfig', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn(
				async () =>
					new Response(JSON.stringify({ healthPath: '/health' }), {
						status: 200,
						headers: { 'Content-Type': 'application/json' },
					}),
			),
		);

		await loadAppConfig();

		expect(window.__APP_CONFIG__).toEqual({ healthPath: '/health' });
		expect(getAppConfig().healthPath).toBe('/health');
	});

	it('leaves combined-mode defaults in place when the fetch fails', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn(async () => {
				throw new Error('network down');
			}),
		);

		await loadAppConfig();

		expect(getAppConfig().healthPath).toBe('/admin/health');
	});

	it('ignores a non-OK response and keeps the defaults', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn(async () => new Response('nope', { status: 404 })),
		);

		await loadAppConfig();

		expect(getAppConfig().healthPath).toBe('/admin/health');
	});
});
