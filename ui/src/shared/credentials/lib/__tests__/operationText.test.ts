import { describe, it, expect } from 'vitest';
import { describeFromMethodAndPath, operationText } from '@/shared/credentials/lib/actionRules';

describe('describeFromMethodAndPath — plain English from method and path', () => {
	it.each([
		// adhook.io (most of its 139 operations carry no summary)
		['POST', '/v1/boardColumns', 'Create a board column'],
		['GET', '/v1/boardColumns', 'List board columns'],
		['GET', '/v1/boardColumns/{id}', 'Get a board column'],
		['PUT', '/v1/customEvents/{id}', 'Update a custom event'],
		['PUT', '/v1/customEvents/externalId/{externalId}', 'Update a custom event by external ID'],
		['DELETE', '/v1/fcmDeviceToken/{deviceToken}', 'Delete an FCM device token'],
		['POST', '/v1/fcmDeviceToken', 'Create an FCM device token'],
		['GET', '/v1/notifications/count', 'Get a notification count'],
		['GET', '/v1/influencers', 'List influencers'],
		['DELETE', '/v1/influencers/{id}', 'Delete an influencer'],
		['POST', '/v1/listings/{id}/images', 'Create a listing image'],
		['PUT', '/v1/listings/{id}/opening-hours', 'Update a listing opening hour'],
		['POST', '/v1/promotions/{id}/activate', 'Activate a promotion'],
		['GET', '/v1/instagram/verifyAccount', 'Verify an account'],
		// 1lookup.io
		['POST', '/hlr-lookup', 'Create an HLR lookup'],
		['POST', '/ip', 'Create an IP'],
		['GET', '/account', 'Get an account'],
		['POST', '/b2b-contact-append', 'Create a B2B contact append'],
	])('%s %s → %s', (method, path, expected) => {
		expect(describeFromMethodAndPath(method, path)).toBe(expected);
	});

	it('is deterministic and never empty', () => {
		expect(describeFromMethodAndPath('GET', '/')).toBe('Get the root resource');
		expect(describeFromMethodAndPath('get', '/v1/{id}')).toBe('Get the root resource');
	});
});

describe('operationText — summary, then description, then generated', () => {
	it('prefers the summary, then the first sentence of the description', () => {
		expect(operationText({ method: 'GET', path: '/x', name: 'List x' })).toEqual({
			text: 'List x',
			generated: false,
		});
		expect(
			operationText({
				method: 'PUT',
				path: '/v1/customEvents/{id}',
				description: 'Updates a customEvent. Requires admin.',
			}),
		).toEqual({ text: 'Updates a customEvent', generated: false });
	});

	it('falls back to a phrase described from the method and path, marked generated', () => {
		expect(
			operationText({ method: 'POST', path: '/v1/boardColumns', description: '' }),
		).toEqual({ text: 'Create a board column', generated: true });
	});
});
