import { describe, it, expect } from 'vitest';
import {
	alternatesHint,
	alternatesLabel,
	isGitHubUrl,
	spansSources,
	versionLabel,
} from '@/modules/discover/lib/catalogSpec';

describe('versionLabel', () => {
	it('prefixes a bare version with "v" and leaves an existing prefix alone', () => {
		expect(versionLabel('2024-01-01')).toBe('v2024-01-01');
		expect(versionLabel('1.0.0')).toBe('v1.0.0');
		expect(versionLabel('v3')).toBe('v3');
	});
});

describe('alternates', () => {
	it('labels the collapsed entries with a singular/plural count', () => {
		expect(alternatesLabel(1)).toBe('+1 other source');
		expect(alternatesLabel(3)).toBe('+3 other sources');
	});

	it('explains the collapse in the tooltip', () => {
		expect(alternatesHint(1)).toBe(
			'Another catalog source lists this API too; this entry is the one shown.',
		);
		expect(alternatesHint(2)).toBe(
			'2 other catalog sources list this API too; this entry is the one shown.',
		);
	});
});

describe('isGitHubUrl', () => {
	it('recognises GitHub pages only', () => {
		expect(isGitHubUrl('https://github.com/jentic/x/stripe.com')).toBe(true);
		expect(isGitHubUrl('https://gist.github.com/x')).toBe(true);
		expect(isGitHubUrl('https://apis.internal.example/stripe')).toBe(false);
		expect(isGitHubUrl('https://github.com.evil.example/x')).toBe(false);
		expect(isGitHubUrl('not a url')).toBe(false);
	});
});

describe('spansSources', () => {
	it('is true only when the entries come from more than one source', () => {
		expect(spansSources([])).toBe(false);
		expect(spansSources([{ sourceSlug: 'public' }, { sourceSlug: 'public' }])).toBe(false);
		expect(spansSources([{ sourceSlug: 'internal' }])).toBe(false);
		expect(spansSources([{ sourceSlug: 'internal' }, { sourceSlug: 'public' }])).toBe(true);
	});
});
