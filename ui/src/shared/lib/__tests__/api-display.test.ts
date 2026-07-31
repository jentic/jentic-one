import { describe, expect, it } from 'vitest';
import {
	apiRefDisplayName,
	humanizeDomainSlug,
	humanizeName,
	titleFromApiId,
	toolkitCredDisplayName,
} from '../api-display';

/**
 * These tests pin the two humanising rules (`humanizeDomainSlug` for a
 * `<vendor>` identity, `humanizeName` for a conservative product / sub-API
 * name) applied everywhere an API's machine identity needs to render as a
 * friendly primary line. Discover consumed the same rule before it moved here;
 * its own adapter tests still exist as a guardrail that this extraction didn't
 * shift Discover behaviour.
 */

describe('humanizeDomainSlug', () => {
	it('title-cases underscore/hyphen/dot separated tokens', () => {
		expect(humanizeDomainSlug('article_search')).toBe('Article Search');
		expect(humanizeDomainSlug('top-stories')).toBe('Top Stories');
		expect(humanizeDomainSlug('v2')).toBe('V2');
	});

	it('preserves a real dotted TLD suffix as a dot join', () => {
		// A raw dotted domain keeps its dot: `posthog.com` reads `Posthog.Com`.
		expect(humanizeDomainSlug('posthog.com')).toBe('Posthog.Com');
		// A multi-part dotted domain keeps ALL its dots rather than collapsing
		// the leading labels to spaces — `foo.bar.com` reads `Foo.Bar.Com`, not
		// the old `Foo Bar.Com` which misjoined the leading label.
		expect(humanizeDomainSlug('foo.bar.com')).toBe('Foo.Bar.Com');
		expect(humanizeDomainSlug('nytimes.com')).toBe('Nytimes.Com');
	});

	it('keeps a multi-part public suffix dotted rather than misjoining it', () => {
		// A compound public suffix (`co.uk`) must not collapse to `Bbc Co.Uk`
		// or lose structure — the whole domain stays dot-joined across labels.
		expect(humanizeDomainSlug('bbc.co.uk')).toBe('Bbc.Co.Uk');
		expect(humanizeDomainSlug('example.co.uk')).toBe('Example.Co.Uk');
	});

	it('dot-joins a 2-token hyphenated domain slug (real vendor data is hyphenated)', () => {
		// Real vendor slugs in this app are hyphenated (`posthog-com`), not
		// dotted. A 2-token `<vendor>-<tld>` slug is a domain, so it dot-joins by
		// default — restoring the original #631 requirement.
		expect(humanizeDomainSlug('posthog-com')).toBe('Posthog.Com');
		expect(humanizeDomainSlug('github-com')).toBe('Github.Com');
		expect(humanizeDomainSlug('nytimes-com')).toBe('Nytimes.Com');
	});

	it('does NOT dot-join a 3+-token hyphenated product slug whose last token looks like a TLD', () => {
		// 3+ tokens is a product name, not a domain, so it stays space-joined —
		// killing the `stable-diffusion-ai` → `Stable Diffusion.Ai` false positive.
		expect(humanizeDomainSlug('stable-diffusion-ai')).toBe('Stable Diffusion Ai');
		expect(humanizeDomainSlug('foo-bar-io')).toBe('Foo Bar Io');
	});

	it('rejoins each allowlist suffix with a dot for a 2-token slug and for dotted input', () => {
		for (const suffix of ['com', 'org', 'net', 'io', 'dev', 'ai', 'app', 'co', 'xyz']) {
			expect(humanizeDomainSlug(`acme.${suffix}`)).toBe(`Acme.${capitalize(suffix)}`);
			// A 2-token hyphenated slug dot-joins by default (domain-slug shape).
			expect(humanizeDomainSlug(`acme-${suffix}`)).toBe(`Acme.${capitalize(suffix)}`);
		}
	});

	it('leaves a non-allowlist trailing token space-joined', () => {
		// "biz" is not on the allowlist, so it stays space-joined even when dotted.
		expect(humanizeDomainSlug('acme.biz')).toBe('Acme Biz');
	});

	it('returns single-token input title-cased with no separators', () => {
		expect(humanizeDomainSlug('github')).toBe('Github');
	});

	it('handles empty/all-separator input without crashing', () => {
		expect(humanizeDomainSlug('')).toBe('');
		expect(humanizeDomainSlug('---')).toBe('');
	});
});

describe('humanizeName', () => {
	it('stays conservative (dot only on a literal dot) — a hyphenated slug never dot-joins', () => {
		// The sub-API path (`titleFromApiId`) uses this conservative rule, so a
		// 2-token hyphenated endpoint name stays space-joined; only a genuinely
		// dotted input keeps its dot.
		expect(humanizeName('posthog-com')).toBe('Posthog Com');
		expect(humanizeName('bar-io')).toBe('Bar Io');
		expect(humanizeName('posthog.com')).toBe('Posthog.Com');
	});

	it('title-cases underscore/hyphen separated tokens', () => {
		expect(humanizeName('article_search')).toBe('Article Search');
		expect(humanizeName('stable-diffusion-ai')).toBe('Stable Diffusion Ai');
	});

	it('handles empty/all-separator input without crashing', () => {
		expect(humanizeName('')).toBe('');
		expect(humanizeName('---')).toBe('');
	});
});

describe('titleFromApiId', () => {
	it('extracts and title-cases the sub-API segment', () => {
		expect(titleFromApiId('nytimes.com/article_search')).toBe('Article Search');
		expect(titleFromApiId('acme.com/top-stories')).toBe('Top Stories');
	});

	it('returns the api_id unchanged for a bare-domain input', () => {
		// The bare-domain fallback fires BEFORE the humaniser runs, so the TLD
		// suffix rule cannot upgrade `stripe.com` to `Stripe.Com` — that would be
		// a behaviour change vs. Discover today, which we explicitly do not want.
		expect(titleFromApiId('stripe.com')).toBe('stripe.com');
		expect(titleFromApiId('github.com')).toBe('github.com');
		expect(titleFromApiId('slack.com')).toBe('slack.com');
	});

	it('title-cases hyphen/underscore mixes in the sub-segment', () => {
		expect(titleFromApiId('foo.com/bar-baz_qux')).toBe('Bar Baz Qux');
	});

	it('does NOT dot-join a hyphenated sub-API segment whose token looks like a TLD', () => {
		// Sub-API segments are endpoint/product names, not domain slugs, so
		// `titleFromApiId` runs `humanizeName` (the conservative rule): a
		// trailing allowlist TLD only rejoins with a dot when the raw segment
		// carried a real dot. A hyphenated sub-segment (`bar-io`, even 2-token)
		// stays space-joined.
		expect(titleFromApiId('acme.com/bar-io')).toBe('Bar Io');
		expect(titleFromApiId('acme.com/foo-com')).toBe('Foo Com');
		expect(titleFromApiId('acme.com/foo-bar-io')).toBe('Foo Bar Io');
	});

	it('leaves a non-allowlist trailing token in the sub-segment space-joined', () => {
		// `biz` is not on the TLD allowlist, so no dot-rejoin — stays a space.
		expect(titleFromApiId('acme.com/foo-biz')).toBe('Foo Biz');
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

	it('renders one consistent string when api_name equals api_vendor', () => {
		// When the sub-API name is exactly the vendor there's no distinguishing
		// segment, so both fields collapse to the SAME single vendor
		// humanisation rather than `Github Com` (name path) vs `Github.Com`
		// (vendor path).
		expect(toolkitCredDisplayName({ api_vendor: 'github-com', api_name: 'github-com' })).toBe(
			'Github.Com',
		);
		expect(toolkitCredDisplayName({ api_vendor: 'stripe', api_name: 'stripe' })).toBe('Stripe');
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

	it('returns a padded displayName trimmed, not verbatim', () => {
		// The gate trims, so the *return* must be trimmed too — otherwise padded
		// labels leak into headings and the seeded credential name.
		expect(
			apiRefDisplayName({ displayName: '  GitHub REST API  ', vendor: 'github.com' }),
		).toBe('GitHub REST API');
	});

	it('treats a whitespace-only displayName as absent and falls through', () => {
		// A whitespace-only string is truthy, so a naive `if (displayName)`
		// guard would render a blank primary line. The trim-guard skips it and
		// falls through to the vendor humanisation instead. Real vendor slugs
		// are hyphenated, so the 2-token domain-slug rule dot-joins them.
		expect(apiRefDisplayName({ displayName: '   ', vendor: 'github-com' })).toBe('Github.Com');
		expect(apiRefDisplayName({ displayName: '\t\n ', vendor: 'nytimes-com' })).toBe(
			'Nytimes.Com',
		);
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

	it('strips a space/colon/pipe-separated vendor prefix so the vendor is not double-rendered', () => {
		// The separator after the vendor prefix isn't always a hyphen/dot — a
		// space (and colon / pipe) must also be treated as a separator, else the
		// prefix escapes the strip and the vendor renders twice.
		expect(
			apiRefDisplayName({ displayName: null, vendor: 'stripe', name: 'stripe:payments-api' }),
		).toBe('Payments Api');
		expect(
			apiRefDisplayName({ displayName: null, vendor: 'slack', name: 'slack|events-api' }),
		).toBe('Events Api');
	});

	it('peels a leading TLD token when the name mirrors the vendor DOMAIN, not the bare slug', () => {
		// `vendor='posthog'` but the name mirrors `posthog.com` — stripping only
		// the vendor slug would orphan the TLD (`Com Posthog Api`), the exact
		// double-render bug class the strip exists to prevent, shifted one
		// token right. The leading TLD peels as part of the vendor prefix.
		expect(
			apiRefDisplayName({
				displayName: null,
				vendor: 'posthog',
				name: 'posthog com posthog-api',
			}),
		).toBe('Posthog Api');
		expect(
			apiRefDisplayName({ displayName: null, vendor: 'nytimes', name: 'nytimes-com-books' }),
		).toBe('Books');
	});

	it('falls back to the vendor when the strip consumes the whole name (TLD-only remainder)', () => {
		// `vendor='posthog'`, `name='posthog-com'`: the name is just the
		// vendor's domain form, so nothing distinguishing remains — render the
		// vendor (`Posthog`), never the orphaned remainder (`Com`).
		expect(
			apiRefDisplayName({ displayName: null, vendor: 'posthog', name: 'posthog-com' }),
		).toBe('Posthog');
	});

	it('handles uppercase input case-insensitively', () => {
		// The prefix match is case-insensitive and title-casing normalises the
		// first letter only, so shouting payloads still strip and render.
		expect(
			apiRefDisplayName({
				displayName: null,
				vendor: 'POSTHOG-COM',
				name: 'POSTHOG-COM-API',
			}),
		).toBe('API');
		expect(apiRefDisplayName({ displayName: null, vendor: 'GITHUB-COM', name: 'main' })).toBe(
			'GITHUB.COM',
		);
	});

	it('is idempotent: feeding an output back in does not change it', () => {
		// Display names round-trip through forms (the seeded credential name);
		// re-deriving from an already-humanised string must be a no-op.
		const once = apiRefDisplayName({
			displayName: null,
			vendor: 'nytimes-com',
			name: 'nytimes-com-article-search',
		});
		expect(apiRefDisplayName({ displayName: null, vendor: 'nytimes-com', name: once })).toBe(
			once,
		);
		const vendorOnly = apiRefDisplayName({
			displayName: null,
			vendor: 'posthog-com',
			name: '',
		});
		expect(
			apiRefDisplayName({ displayName: vendorOnly, vendor: 'posthog-com', name: '' }),
		).toBe(vendorOnly);
	});

	it('agrees with toolkitCredDisplayName and titleFromApiId on one identity', () => {
		// Cross-helper consistency pin: the same umbrella sub-API must render
		// identically whichever DTO shape a surface happens to hold.
		const fromRef = apiRefDisplayName({
			displayName: null,
			vendor: 'nytimes-com',
			name: 'nytimes-com-article-search',
		});
		const fromBinding = toolkitCredDisplayName({
			api_vendor: 'nytimes-com',
			api_name: 'nytimes-com/nytimes-com-article-search',
		});
		const fromApiId = titleFromApiId('nytimes.com/article-search');
		expect(fromRef).toBe('Article Search');
		expect(fromBinding).toBe(fromRef);
		expect(fromApiId).toBe(fromRef);
	});

	it('falls back to a humanised vendor when name is a generic placeholder', () => {
		expect(apiRefDisplayName({ displayName: null, vendor: 'stripe-com', name: 'main' })).toBe(
			'Stripe.Com',
		);
	});

	it('renders one consistent string when name equals vendor', () => {
		// Same identity, one rendering: `name === vendor` has no distinguishing
		// sub-API, so it falls through to the single vendor humanisation
		// (`Github.Com`) instead of the name path's `Github Com`.
		expect(
			apiRefDisplayName({ displayName: null, vendor: 'github-com', name: 'github-com' }),
		).toBe('Github.Com');
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
