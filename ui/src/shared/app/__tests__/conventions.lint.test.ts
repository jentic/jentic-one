import { describe, expect, it } from 'vitest';
import { ESLint } from 'eslint';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

/**
 * Proves the convention guardrails actually bite (#510 + #511).
 *
 * It's not enough to migrate the code and watch `npm run lint` pass — a green
 * tree only shows there are no violations *today*. This suite feeds KNOWN-BAD
 * snippets to the real ESLint config and asserts each is rejected, and feeds
 * the sanctioned alternative and asserts it's accepted. So if a future config
 * refactor silently drops a rule (turning the guardrail into a no-op), this
 * fails — the enforcement is itself under test.
 *
 * Uses ESLint's programmatic API against the project's real `eslint.config.js`
 * (resolved from the ui/ root), which is why this is a node-mode test
 * (`vitest.lint.config.ts`) rather than a browser-mode component test.
 */
const uiRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../../../..');
const eslint = new ESLint({ cwd: uiRoot });

async function lint(filePath: string, code: string) {
	const [result] = await eslint.lintText(code, { filePath: resolve(uiRoot, filePath) });
	return result.messages;
}

const ruleIds = (messages: Awaited<ReturnType<typeof lint>>) => messages.map((m) => m.ruleId);

describe('#510 — no hardcoded /app client paths', () => {
	it('rejects a string literal that starts with /app/', async () => {
		const messages = await lint(
			'src/modules/dashboard/components/Bad.tsx',
			"export const to = '/app/discover';\n",
		);
		expect(ruleIds(messages)).toContain('no-restricted-syntax');
		expect(messages.some((m) => m.message.includes('/app'))).toBe(true);
	});

	it('rejects the bare /app literal', async () => {
		const messages = await lint(
			'src/modules/dashboard/components/Bad.tsx',
			"export const home = '/app';\n",
		);
		expect(ruleIds(messages)).toContain('no-restricted-syntax');
	});

	it('accepts a root-relative path literal (the basename adds /app)', async () => {
		const messages = await lint(
			'src/modules/dashboard/components/Good.tsx',
			"export const to = '/discover';\n",
		);
		expect(ruleIds(messages)).not.toContain('no-restricted-syntax');
	});

	it('rejects a /app template literal (the likely evasion of the Literal rule)', async () => {
		const messages = await lint(
			'src/modules/dashboard/components/Bad.tsx',
			'export const to = (id: string) => `/app/agents/${id}`;\n',
		);
		expect(ruleIds(messages)).toContain('no-restricted-syntax');
		expect(messages.some((m) => m.message.includes('/app'))).toBe(true);
	});
});

describe('#511 — no foreign-module query-key literals', () => {
	it("rejects a ['workspace', …] key literal written inside the discover module", async () => {
		const messages = await lint(
			'src/modules/discover/api/bad.ts',
			"export const k = ['workspace', 'apis'];\n",
		);
		expect(ruleIds(messages)).toContain('no-restricted-syntax');
		expect(messages.some((m) => m.message.includes('sharedQueryKeys'))).toBe(true);
	});

	it("rejects a ['credentials', …] key literal written inside the agents module", async () => {
		const messages = await lint(
			'src/modules/agents/api/bad.ts',
			"export const k = ['credentials', 'apis', 'list'];\n",
		);
		expect(ruleIds(messages)).toContain('no-restricted-syntax');
	});

	it("accepts a module's OWN root key literal", async () => {
		const messages = await lint(
			'src/modules/discover/api/good.ts',
			"export const k = ['discover', 'catalog'];\n",
		);
		expect(ruleIds(messages)).not.toContain('no-restricted-syntax');
	});

	// The agents module owns TWO roots ('agents' AND 'service-accounts'), so its
	// second root is the most error-prone branch of the own/foreign set math:
	// a sibling must be blocked from it, yet agents itself must stay free.
	it("rejects a sibling's ['service-accounts', …] root (agents' second root)", async () => {
		const messages = await lint(
			'src/modules/discover/api/bad.ts',
			"export const k = ['service-accounts', 'list'];\n",
		);
		expect(ruleIds(messages)).toContain('no-restricted-syntax');
		expect(messages.some((m) => m.message.includes('service-accounts'))).toBe(true);
	});

	it("accepts the agents module using its OWN second root ['service-accounts', …]", async () => {
		const messages = await lint(
			'src/modules/agents/api/good.ts',
			"export const k = ['service-accounts', 'list'];\n",
		);
		expect(ruleIds(messages)).not.toContain('no-restricted-syntax');
	});

	it('does not apply the key rule to shared/ (registry lives there)', async () => {
		const messages = await lint(
			'src/shared/api/bad.ts',
			"export const k = ['workspace', 'apis'];\n",
		);
		expect(ruleIds(messages)).not.toContain('no-restricted-syntax');
	});
});
