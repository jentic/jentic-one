import { describe, expect, it } from 'vitest';
import {
	enhancedScopesFromSchemes,
	extractResourceFromScope,
	filterScopeGroups,
	formatResourceName,
	getRecommendedScopes,
	groupScopesByResource,
	isRecommendedScope,
	scopesInGroup,
	type EnhancedScope,
} from '@/modules/credentials/lib/scope-utils';

function scope(name: string, recommended = false, description = ''): EnhancedScope {
	return { scope: name, description, origin: 'schema', isRecommended: recommended };
}

describe('credentials/lib/scope-utils', () => {
	describe('extractResourceFromScope', () => {
		it('splits on the first delimiter', () => {
			expect(extractResourceFromScope('read:jira')).toBe('read');
			expect(extractResourceFromScope('repo.admin')).toBe('repo');
			expect(extractResourceFromScope('files/read')).toBe('files');
			expect(extractResourceFromScope('user_email')).toBe('user');
		});
		it('handles URL-style scopes by trailing path segment', () => {
			expect(extractResourceFromScope('https://www.googleapis.com/auth/calendar')).toBe(
				'calendar',
			);
		});
		it('returns the whole scope when there is no delimiter', () => {
			expect(extractResourceFromScope('openid')).toBe('openid');
		});
	});

	describe('formatResourceName', () => {
		it('title-cases and splits on separators', () => {
			expect(formatResourceName('read-only')).toBe('Read Only');
			expect(formatResourceName('user_email')).toBe('User Email');
			expect(formatResourceName('other')).toBe('Other');
		});
	});

	describe('isRecommendedScope', () => {
		it('recommends read-only scopes', () => {
			expect(isRecommendedScope('read:user')).toBe(true);
			expect(isRecommendedScope('calendar.readonly')).toBe(true);
			expect(isRecommendedScope('openid')).toBe(true);
		});
		it('never recommends write/admin scopes (even if also read)', () => {
			expect(isRecommendedScope('write:user')).toBe(false);
			expect(isRecommendedScope('admin:read')).toBe(false);
			expect(isRecommendedScope('master:read')).toBe(false);
			expect(isRecommendedScope('readwrite')).toBe(false);
		});
		it('does not false-positive on substrings', () => {
			expect(isRecommendedScope('spreadsheets')).toBe(false);
		});
	});

	describe('groupScopesByResource', () => {
		it('groups by resource prefix, alphabetically, with sorted scopes', () => {
			const groups = groupScopesByResource([
				scope('write:jira'),
				scope('read:jira'),
				scope('read:confluence'),
			]);
			expect(groups.map((g) => g.id)).toEqual(['read', 'write']);
			const read = groups.find((g) => g.id === 'read')!;
			expect(read.scopes.map((s) => s.scope)).toEqual(['read:confluence', 'read:jira']);
			expect(read.totalCount).toBe(2);
		});
	});

	describe('scopesInGroup', () => {
		it('returns scope names whose resource matches the group id', () => {
			const all = [scope('read:jira'), scope('read:confluence'), scope('write:jira')];
			expect(scopesInGroup(all, 'read')).toEqual(['read:jira', 'read:confluence']);
		});
	});

	describe('filterScopeGroups', () => {
		it('keeps only matching scopes and drops empty groups', () => {
			const groups = groupScopesByResource([
				scope('read:jira', false, 'Read issues'),
				scope('write:jira'),
			]);
			const filtered = filterScopeGroups(groups, 'issues');
			expect(filtered).toHaveLength(1);
			expect(filtered[0].scopes.map((s) => s.scope)).toEqual(['read:jira']);
		});
		it('returns all groups for an empty query', () => {
			const groups = groupScopesByResource([scope('read:jira')]);
			expect(filterScopeGroups(groups, '  ')).toEqual(groups);
		});
	});

	describe('getRecommendedScopes', () => {
		it('returns only the recommended subset', () => {
			const list = [scope('read:jira', true), scope('write:jira', false)];
			expect(getRecommendedScopes(list).map((s) => s.scope)).toEqual(['read:jira']);
		});
	});

	describe('enhancedScopesFromSchemes', () => {
		it('flattens oauth2 flow scopes, sorts, and flags recommendations', () => {
			const schemes = {
				oauth: {
					type: 'oauth2',
					flows: {
						authorizationCode: {
							scopes: { 'write:jira': 'Write', 'read:jira': 'Read issues' },
						},
					},
				},
			} as never;
			const scopes = enhancedScopesFromSchemes(schemes);
			expect(scopes.map((s) => s.scope)).toEqual(['read:jira', 'write:jira']);
			const read = scopes.find((s) => s.scope === 'read:jira')!;
			expect(read).toMatchObject({
				description: 'Read issues',
				origin: 'schema',
				isRecommended: true,
			});
			expect(scopes.find((s) => s.scope === 'write:jira')!.isRecommended).toBe(false);
		});
		it('returns [] for non-oauth specs', () => {
			expect(enhancedScopesFromSchemes({ k: { type: 'apiKey' } })).toEqual([]);
		});
	});
});
