import { describe, expect, it } from 'vitest';
import {
	classifyOpCoverage,
	evaluateTemplateOp,
	generateRuleExamples,
	ruleAppliesToTemplate,
	ruleNarrowsTemplate,
	sampleTemplatePaths,
	templateToRegex,
} from '@/shared/credentials/lib/template-matcher';

/**
 * The UI's ops preview evaluates rules against op TEMPLATES (with
 * ``{placeholder}`` segments) rather than the concrete request paths the
 * enforce-time matcher sees. These tests pin the template-aware
 * semantics — specifically the "an exact rule for a concrete path
 * should mark a placeholder-shaped op as affected" property, which was
 * the whole point of introducing this module.
 */

describe('templateToRegex', () => {
	it('turns placeholders into single-segment wildcards', () => {
		const re = templateToRegex('/repos/{owner}/{repo}');
		expect(re.test('/repos/octocat/hello-world')).toBe(true);
		// Slashes must be matched literally — a segment can't span them.
		expect(re.test('/repos/octocat/hello/world')).toBe(false);
		// Literal parts are exact.
		expect(re.test('/repas/octocat/hello-world')).toBe(false);
	});

	it('escapes regex metacharacters in literal segments', () => {
		const re = templateToRegex('/v1.0/foo');
		expect(re.test('/v1.0/foo')).toBe(true);
		// The dot should not act as wildcard.
		expect(re.test('/v1x0/foo')).toBe(false);
	});
});

describe('ruleAppliesToTemplate', () => {
	it('exact rule matches when the concrete path fits the template', () => {
		const rule = {
			effect: 'allow' as const,
			path: '/repos/octocat/hello-world',
			match_mode: 'exact' as const,
		};
		expect(ruleAppliesToTemplate(rule, '/repos/{owner}/{repo}')).toBe(true);
	});

	it('exact rule does not apply when segment count differs', () => {
		const rule = {
			effect: 'allow' as const,
			path: '/repos/octocat',
			match_mode: 'exact' as const,
		};
		expect(ruleAppliesToTemplate(rule, '/repos/{owner}/{repo}')).toBe(false);
	});

	it('prefix rule aligns with template fixed segments', () => {
		const rule = {
			effect: 'allow' as const,
			path: '/repos/octocat',
			match_mode: 'prefix' as const,
		};
		expect(ruleAppliesToTemplate(rule, '/repos/{owner}/{repo}')).toBe(true);
		expect(ruleAppliesToTemplate(rule, '/users/{login}')).toBe(false);
	});

	it('regex rule intersects with the template via randexp sampling', () => {
		const rule = {
			effect: 'allow' as const,
			// Only octocat's repos, any repo name (letters + hyphens).
			path: '/repos/octocat/[a-z-]+',
			match_mode: 'regex' as const,
		};
		expect(ruleAppliesToTemplate(rule, '/repos/{owner}/{repo}')).toBe(true);
		// Wrong root — regex can never satisfy the template.
		expect(ruleAppliesToTemplate(rule, '/users/{login}')).toBe(false);
	});
});

describe('generateRuleExamples', () => {
	it('returns the rule path verbatim for exact matches', () => {
		expect(
			generateRuleExamples(
				{ effect: 'allow', path: '/repos/octocat/hello-world', match_mode: 'exact' },
				'/repos/{owner}/{repo}',
			),
		).toEqual(['/repos/octocat/hello-world']);
	});

	it('fills placeholders past the prefix with human-readable samples', () => {
		const [example] = generateRuleExamples(
			{ effect: 'allow', path: '/repos/octocat', match_mode: 'prefix' },
			'/repos/{owner}/{repo}',
		);
		expect(example).toBe('/repos/octocat/<repo>');
	});

	it('generates regex examples that satisfy both regex and template', () => {
		const examples = generateRuleExamples(
			{
				effect: 'allow',
				path: '/repos/octocat/[a-z-]+',
				match_mode: 'regex',
			},
			'/repos/{owner}/{repo}',
			3,
		);
		expect(examples.length).toBeGreaterThan(0);
		for (const ex of examples) {
			expect(ex.startsWith('/repos/octocat/')).toBe(true);
		}
	});
});

describe('ruleNarrowsTemplate', () => {
	it('is false for the catch-all prefix rule', () => {
		expect(
			ruleNarrowsTemplate(
				{ effect: 'allow', path: '/', match_mode: 'prefix' },
				'/repos/{owner}/{repo}',
			),
		).toBe(false);
	});

	it('is true for a rule that constrains to a specific owner', () => {
		expect(
			ruleNarrowsTemplate(
				{ effect: 'allow', path: '/repos/octocat', match_mode: 'prefix' },
				'/repos/{owner}/{repo}',
			),
		).toBe(true);
	});
});

describe('sampleTemplatePaths', () => {
	it('returns the template itself when it has no placeholders', () => {
		expect(sampleTemplatePaths('/user', [])).toEqual(['/user']);
	});

	it('mixes generic + rule-derived candidates', () => {
		// The sampler should pick up ``jentic`` from the rule's owner
		// position so a downstream classifier can see the rule's coverage.
		const samples = sampleTemplatePaths(
			'/repos/{owner}/{repo}/commits',
			[
				{
					effect: 'allow',
					path: '/repos/jentic/jentic-one',
					match_mode: 'prefix',
				},
			],
			8,
		);
		expect(samples.some((s) => s.startsWith('/repos/jentic/'))).toBe(true);
		expect(samples.some((s) => s.startsWith('/repos/example-owner/'))).toBe(true);
	});
});

describe('classifyOpCoverage', () => {
	it('reports "allow" when a placeholder rule fully covers the op', () => {
		const result = classifyOpCoverage(
			[
				{
					effect: 'allow',
					methods: ['GET'],
					path: '/repos/{owner}/{repo}/commits',
					match_mode: 'prefix',
				},
			],
			{ method: 'GET', path: '/repos/{owner}/{repo}/commits', operation_id: null },
		);
		expect(result.verdict).toBe('allow');
	});

	it('reports "partial" when only a narrow rule covers the op', () => {
		// Only jentic/jentic-one is allowed — every other owner defaults
		// to deny. Regression guard for the exact case from the issue
		// (rule A alone must show partial with sample paths for both
		// buckets).
		const result = classifyOpCoverage(
			[
				{
					effect: 'allow',
					methods: null,
					path: '/repos/jentic/jentic-one',
					match_mode: 'prefix',
				},
			],
			{ method: 'GET', path: '/repos/{owner}/{repo}/commits', operation_id: null },
		);
		expect(result.verdict).toBe('partial');
		expect(result.allowedSamples.length).toBeGreaterThan(0);
		expect(result.deniedSamples.length).toBeGreaterThan(0);
		for (const s of result.allowedSamples) {
			expect(s.startsWith('/repos/jentic/jentic-one/')).toBe(true);
		}
	});

	it('reports "deny" when no rule applies', () => {
		const result = classifyOpCoverage([], {
			method: 'GET',
			path: '/users/{login}',
			operation_id: null,
		});
		expect(result.verdict).toBe('deny');
	});
});

describe('evaluateTemplateOp', () => {
	it('picks the first matching rule and reports it', () => {
		const rules = [
			{
				effect: 'deny' as const,
				methods: ['DELETE'],
				path: null,
				match_mode: 'regex' as const,
			},
			{
				effect: 'allow' as const,
				methods: ['GET'],
				path: '/repos/octocat',
				match_mode: 'prefix' as const,
			},
		];
		const result = evaluateTemplateOp(rules, {
			method: 'GET',
			path: '/repos/{owner}/{repo}',
			operation_id: 'repos/get',
		});
		expect(result.allowed).toBe(true);
		expect(result.matchingRule).toBe(rules[1]);
	});

	it('defaults to deny with no matching rule', () => {
		const result = evaluateTemplateOp([], {
			method: 'GET',
			path: '/users/{login}',
			operation_id: 'users/get',
		});
		expect(result.allowed).toBe(false);
		expect(result.matchingRule).toBeNull();
	});
});
