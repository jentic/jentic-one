import { describe, expect, it } from 'vitest';
import {
	apiRefDisplayName,
	humanizeSegment,
	humanizeVendor,
	titleFromApiId,
	toolkitCredDisplayName,
} from '../api-display';

/**
 * These tests pin the single humanising rule (`humanizeSegment`) applied
 * everywhere an API's machine identity needs to render as a friendly primary
 * line. Discover consumed the same rule before it moved here; its own adapter
 * tests still exist as a guardrail that this extraction didn't shift Discover
 * behaviour.
 *
 * See `docs/plans/issue-631-friendly-api-names.md` for the worked examples
 * across the four consuming surfaces.
 */

describe('humanizeSegment', () => {
	it('title-cases underscore/hyphen/dot separated tokens', () => {
		expect(humanizeSegment('article_search')).toBe('Article Search');
		expect(humanizeSegment('top-stories')).toBe('Top Stories');
		expect(humanizeSegment('v2')).toBe('V2');
	});

	it('preserves a common TLD suffix as a dot join', () => {
		expect(humanizeSegment('posthog-com')).toBe('Posthog.Com');
		expect(humanizeSegment('foo-bar-com')).toBe('Foo Bar.Com');
		expect(humanizeSegment('nytimes-com')).toBe('Nytimes.Com');
	});

	it('rejoins each allowlist suffix with a dot', () => {
		for (const suffix of ['com', 'org', 'net', 'io', 'dev', 'ai', 'app', 'co', 'xyz']) {
			expect(humanizeSegment(`acme-${suffix}`)).toBe(`Acme.${capitalize(suffix)}`);
		}
	});

	it('leaves a non-allowlist trailing token space-joined', () => {
		// "biz" is not on the allowlist, so it stays space-joined.
		expect(humanizeSegment('acme-biz')).toBe('Acme Biz');
	});

	it('returns single-token input title-cased with no separators', () => {
		expect(humanizeSegment('github')).toBe('Github');
	});

	it('handles empty/all-separator input without crashing', () => {
		expect(humanizeSegment('')).toBe('');
		expect(humanizeSegment('---')).toBe('');
	});
});

describe('titleFromApiId', () => {
	it('extracts and title-cases the sub-API segment', () => {
		expect(titleFromApiId('nytimes.com/article_search')).toBe('Article Search');
		expect(titleFromApiId('acme.com/top-stories')).toBe('Top Stories');
	});

	it('returns the api_id unchanged for a bare-domain input', () => {
		// The bare-domain fallback fires BEFORE humanizeSegment runs, so the TLD
		// suffix rule cannot upgrade `stripe.com` to `Stripe.Com` — that would be
		// a behaviour change vs. Discover today, which we explicitly do not want.
		expect(titleFromApiId('stripe.com')).toBe('stripe.com');
		expect(titleFromApiId('github.com')).toBe('github.com');
		expect(titleFromApiId('slack.com')).toBe('slack.com');
	});

	it('title-cases hyphen/underscore mixes in the sub-segment', () => {
		expect(titleFromApiId('foo.com/bar-baz_qux')).toBe('Bar Baz Qux');
	});
});

describe('humanizeVendor', () => {
	it('is an alias of humanizeSegment', () => {
		expect(humanizeVendor('posthog-com')).toBe('Posthog.Com');
		expect(humanizeVendor('github')).toBe('Github');
	});
});

describe('toolkitCredDisplayName', () => {
	it('strips a repeated vendor prefix from the sub-API segment', () => {
		// Real-world umbrella-vendor payload — the sub-API name mirrors the
		// vendor. Stripping the prefix yields the Discover-style result
		// (`Posthog Api`) instead of `Posthog Com Posthog Api`.
		expect(
			toolkitCredDisplayName({
				api_vendor: 'posthog-com',
				api_name: 'posthog-com/posthog-com-posthog-api',
			}),
		).toBe('Posthog Api');
	});

	it('renders the sub-API name humanised when the vendor prefix does not match', () => {
		expect(
			toolkitCredDisplayName({
				api_vendor: 'nytimes-com',
				api_name: 'nytimes-com/nytimes-com-article-search',
			}),
		).toBe('Article Search');
	});

	it('falls back to a humanised vendor when the name is a generic placeholder', () => {
		expect(toolkitCredDisplayName({ api_vendor: 'stripe-com', api_name: 'main' })).toBe(
			'Stripe.Com',
		);
		expect(toolkitCredDisplayName({ api_vendor: 'posthog-com', api_name: '' })).toBe(
			'Posthog.Com',
		);
	});

	it('humanises a bare api_name when api_vendor is null', () => {
		expect(toolkitCredDisplayName({ api_vendor: null, api_name: 'article_search' })).toBe(
			'Article Search',
		);
	});

	it('returns an empty string when both fields are absent', () => {
		expect(toolkitCredDisplayName({})).toBe('');
		expect(toolkitCredDisplayName({ api_vendor: null, api_name: null })).toBe('');
	});

	it('does not surface a generic api_name as the title when api_vendor is null', () => {
		expect(toolkitCredDisplayName({ api_vendor: null, api_name: 'main' })).toBe('');
		expect(toolkitCredDisplayName({ api_vendor: null, api_name: 'default' })).toBe('');
	});
});

describe('apiRefDisplayName', () => {
	it('returns the explicit displayName verbatim when set', () => {
		expect(
			apiRefDisplayName({
				displayName: 'GitHub REST API',
				vendor: 'github-com',
				name: 'rest',
			}),
			// A user-set label is never overwritten — even when we could have
			// rendered a friendlier vendor humanisation.
		).toBe('GitHub REST API');
	});

	it('strips a repeated vendor prefix from name so it matches Discover output', () => {
		// The whole point of the strip: workspace tiles for umbrella sub-APIs
		// now agree with Discover's `titleFromApiId` on the same API.
		expect(
			apiRefDisplayName({
				displayName: null,
				vendor: 'nytimes-com',
				name: 'nytimes-com-article-search',
			}),
		).toBe('Article Search');
	});

	it('falls back to a humanised vendor when name is a generic placeholder', () => {
		expect(apiRefDisplayName({ displayName: null, vendor: 'stripe-com', name: 'main' })).toBe(
			'Stripe.Com',
		);
	});

	it('humanises the name when the vendor prefix does not match', () => {
		expect(
			apiRefDisplayName({
				displayName: null,
				vendor: 'adyen',
				name: 'pos-terminal-management-api',
			}),
		).toBe('Pos Terminal Management Api');
	});

	it('falls back to a humanised vendor when name is empty', () => {
		expect(apiRefDisplayName({ displayName: null, vendor: 'posthog-com', name: null })).toBe(
			'Posthog.Com',
		);
	});

	it('returns an empty string when nothing usable is present', () => {
		expect(apiRefDisplayName({})).toBe('');
		expect(apiRefDisplayName({ displayName: null, vendor: null, name: null })).toBe('');
	});

	it('does not surface a generic name as the title when the vendor is absent', () => {
		// Regression: the trailing name-humanise branch must honour the same
		// GENERIC_NAMES guard, otherwise a vendor-less `main`/`default` renders
		// as `Main`/`Default` — the exact placeholder this helper filters out.
		expect(apiRefDisplayName({ displayName: null, vendor: null, name: 'main' })).toBe('');
		expect(apiRefDisplayName({ displayName: null, vendor: null, name: 'default' })).toBe('');
	});
});

function capitalize(s: string): string {
	return s.charAt(0).toUpperCase() + s.slice(1);
}
