import { defineConfig, globalIgnores } from 'eslint/config';
import js from '@eslint/js';
import tseslint from 'typescript-eslint';
import pluginReact from 'eslint-plugin-react';
import pluginReactHooks from 'eslint-plugin-react-hooks';
import pluginImportX from 'eslint-plugin-import-x';
import pluginUnusedImports from 'eslint-plugin-unused-imports';
import pluginJsxA11y from 'eslint-plugin-jsx-a11y';
import eslintPluginPrettierRecommended from 'eslint-plugin-prettier/recommended';
import { createTypeScriptImportResolver } from 'eslint-import-resolver-typescript';
import globals from 'globals';
import { readdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = dirname(fileURLToPath(import.meta.url));

// Discover feature modules under src/modules so the sibling-import boundary
// covers every module without a hand-maintained list. For each module we emit
// a zone that forbids it from importing *other* modules (its own files are
// re-allowed via `except`). This is the only reliable way to express
// "no sibling-module imports" in import-x/no-restricted-paths: a single glob
// zone can't distinguish the importer's own module from a sibling.
function moduleBoundaryZones() {
	let modules = [];
	try {
		modules = readdirSync(resolve(root, 'src/modules'), { withFileTypes: true })
			.filter((entry) => entry.isDirectory())
			.map((entry) => entry.name);
	} catch {
		modules = [];
	}
	return modules.map((name) => ({
		target: `./src/modules/${name}`,
		from: './src/modules',
		except: [`./${name}`],
		message: 'A module must not import from sibling modules. Use @/shared.',
	}));
}

// #510: client route paths carry the `/app` prefix exactly once, via the
// router basename (main.tsx) — never as a hardcoded string literal. Flag any
// string literal — OR template literal — that is `/app` or starts with `/app/`,
// steering authors to ROUTES / ROUTE_PATHS. Comments/JSDoc are neither Literal
// nor TemplateLiteral nodes, so the many explanatory `/app` mentions are
// unaffected. (Runtime string concatenation / variable indirection still
// evades this — it's a steering guardrail for the common literal case, not an
// airtight boundary.) The two selectors are spread into each `no-restricted-
// syntax` list that wants the #510 rule.
const NO_HARDCODED_APP_PATH = [
	{
		selector: 'Literal[value=/^\\/app(\\/|$)/]',
		message:
			"Don't hardcode '/app' client paths — use ROUTES / ROUTE_PATHS from @/shared/app (the router basename adds /app).",
	},
	{
		selector: 'TemplateLiteral[quasis.0.value.raw=/^\\/app(\\/|$)/]',
		message:
			"Don't hardcode '/app' client paths (template literal) — use ROUTES / ROUTE_PATHS from @/shared/app (the router basename adds /app).",
	},
];

// ── Cross-module query-key roots (#511) ──────────────────────────────────
// TanStack Query keys are namespaced arrays whose FIRST segment is a module's
// root (e.g. `['workspace', …]`). A module owns its own root, but the
// sibling-import boundary means it can't reference another module's key
// factory — historically it reached in with a raw `['otherModule', …]`
// literal that silently rotted. Those cross-cutting roots now live once in
// `@/shared/api` → `sharedQueryKeys`; this map lets us forbid a module from
// hand-writing a SIBLING's root as an array literal, steering it to the
// registry. Keyed by module dir → the query-key root(s) that module owns.
//
// Unlike the import boundary (auto-derived from `readdirSync`), this map can't
// be inferred — a key root needn't equal its dir name (e.g. `agents` also owns
// `service-accounts`). So `assertModuleRootsCoverDirs()` below fails the lint
// run if a module dir is missing here, forcing a new module to declare its
// roots rather than silently escaping the rule.
const MODULE_QUERY_KEY_ROOTS = {
	workspace: ['workspace'],
	discover: ['discover'],
	toolkits: ['toolkits'],
	credentials: ['credentials'],
	dashboard: ['dashboard'],
	agents: ['agents', 'service-accounts'],
	monitor: ['monitor'],
	docs: ['docs'],
};

// Guard: every module dir under src/modules MUST appear in the map above, so a
// newly-added module can't slip past the #511 cross-module-key rule. Throwing
// here fails `eslint .` loudly (and the dedicated lint-rule test) with a clear
// fix instruction, rather than silently under-enforcing.
function assertModuleRootsCoverDirs() {
	let dirs = [];
	try {
		dirs = readdirSync(resolve(root, 'src/modules'), { withFileTypes: true })
			.filter((entry) => entry.isDirectory())
			.map((entry) => entry.name);
	} catch {
		return; // src/modules unreadable (e.g. tooling sandbox) — skip the guard.
	}
	const missing = dirs.filter((name) => !(name in MODULE_QUERY_KEY_ROOTS));
	if (missing.length > 0) {
		throw new Error(
			`eslint.config.js: module(s) ${missing.join(', ')} missing from ` +
				'MODULE_QUERY_KEY_ROOTS. Add each new module + its query-key root(s) ' +
				'so the #511 cross-module-key rule covers it.',
		);
	}
}

// One `no-restricted-syntax` selector per module: flag an array-expression
// literal whose first element is a STRING equal to a FOREIGN module's root.
// `ArrayExpression > Literal.elements:first-child[value=...]` matches the
// opening `['foreignRoot', …]`; the owning module's own roots are excluded so
// its factory keeps compiling.
function crossModuleKeyOverrides() {
	assertModuleRootsCoverDirs();
	const allRoots = Object.values(MODULE_QUERY_KEY_ROOTS).flat();
	return Object.keys(MODULE_QUERY_KEY_ROOTS).map((name) => {
		const own = new Set(MODULE_QUERY_KEY_ROOTS[name]);
		const foreign = allRoots.filter((r) => !own.has(r));
		return {
			files: [`src/modules/${name}/**/*.{ts,tsx}`],
			ignores: ['**/__tests__/**', '**/*.test.{ts,tsx}'],
			rules: {
				'no-restricted-syntax': [
					'error',
					...NO_HARDCODED_APP_PATH,
					// First-position only: the FIRST array element is the module
					// root, so a foreign root used non-first (e.g. `[x, 'workspace']`)
					// is intentionally NOT flagged — that's not a key-ownership claim.
					...foreign.map((rootKey) => ({
						selector: `ArrayExpression > Literal.elements:first-child[value="${rootKey}"]`,
						message: `Don't hand-write the '${rootKey}' query-key root in another module — invalidate a sibling's cache through @/shared 'sharedQueryKeys' (see #511).`,
					})),
				],
			},
		};
	});
}

export default defineConfig(
	js.configs.recommended,
	tseslint.configs.recommended,

	// ─── Main source rules ───────────────────────────────────────────────
	{
		files: ['**/*.{ts,tsx}'],
		plugins: {
			react: pluginReact,
			'react-hooks': pluginReactHooks,
			'import-x': pluginImportX,
			'unused-imports': pluginUnusedImports,
			'jsx-a11y': pluginJsxA11y,
		},
		languageOptions: {
			ecmaVersion: 'latest',
			sourceType: 'module',
			globals: { ...globals.browser },
			parserOptions: { ecmaFeatures: { jsx: true } },
		},
		settings: {
			react: { version: 'detect' },
			'import-x/resolver-next': [
				createTypeScriptImportResolver({
					alwaysTryTypes: true,
					project: './tsconfig.json',
				}),
			],
		},
		rules: {
			// ── Import hygiene ──────────────────────────────────────────
			'import-x/no-duplicates': 'error',
			'import-x/no-cycle': ['error', { ignoreExternal: true }],
			'import-x/no-self-import': 'error',
			'unused-imports/no-unused-imports': 'error',

			// ── Module boundaries (mirror backend test_module_boundaries) ─
			// Sibling modules may not import each other; everything shared
			// goes through @/shared.
			'import-x/no-restricted-paths': [
				'error',
				{
					zones: [
						{
							target: './src/shared',
							from: './src/modules',
							message: 'shared/ must not import from modules/.',
						},
						...moduleBoundaryZones(),
					],
				},
			],
			// Force absolute @/ imports instead of relative parent traversal.
			'no-restricted-imports': [
				'error',
				{
					patterns: [
						{
							group: ['../*'],
							message: 'Use @/ absolute imports instead of relative parent paths.',
						},
					],
				},
			],

			// ── React ───────────────────────────────────────────────────
			'react/react-in-jsx-scope': 'off',
			'react/prop-types': 'off',
			'react/jsx-pascal-case': 'error',
			'react/jsx-key': 'error',
			'react/button-has-type': 'error',
			'react/function-component-definition': [
				'error',
				{
					namedComponents: 'function-declaration',
					unnamedComponents: 'function-expression',
				},
			],
			'react-hooks/rules-of-hooks': 'error',
			'react-hooks/exhaustive-deps': 'warn',

			// ── Accessibility ───────────────────────────────────────────
			'jsx-a11y/alt-text': 'error',
			'jsx-a11y/anchor-has-content': 'error',
			'jsx-a11y/anchor-is-valid': 'error',
			'jsx-a11y/label-has-associated-control': ['error', { depth: 3 }],

			// ── TypeScript ──────────────────────────────────────────────
			'@typescript-eslint/no-unused-vars': [
				'warn',
				{ argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
			],
			'@typescript-eslint/no-explicit-any': 'warn',
			'@typescript-eslint/consistent-type-imports': ['error', { prefer: 'type-imports' }],

			// ── General ─────────────────────────────────────────────────
			'no-console': ['warn', { allow: ['warn', 'error'] }],
			'no-unused-vars': 'off',

			// ── #510: no hardcoded /app client paths (basename owns it) ──
			'no-restricted-syntax': ['error', ...NO_HARDCODED_APP_PATH],
		},
	},

	// ─── Registry bridge files — exempt from the shared→modules boundary ──
	// The additive registries (route table, MSW root) are the *designed*
	// bridge between the shell and feature modules: each module appends one
	// import + spread line here (see COLLABORATION.md §2–3). This is the only
	// sanctioned place `shared/` references `modules/`, so the
	// shared→modules zone is relaxed for exactly these files.
	{
		files: ['src/shared/app/routes.ts', 'src/mocks/handlers.ts'],
		rules: {
			'import-x/no-restricted-paths': 'off',
		},
	},

	// ─── Feature-module conventions (src/modules/**) ─────────────────────
	// Modules MIRROR the backend's module/layer shape (see CLAUDE.md "Frontend"
	// and COLLABORATION.md §1.5). Two extra disciplines apply to module code
	// (and ONLY module code — `shared/` is exempt because it owns the
	// primitives and would otherwise import its own barrels in a cycle):
	//
	//   1. Barrel discipline — consume shared surfaces through their barrels
	//      (`@/shared`, `@/shared/ui`), never deep paths. Keeps the shared
	//      public API explicit and refactorable, mirroring jentic-webapp.
	//   2. Layering — a module's components/ + pages/ (the "router/view" tier)
	//      reach the backend only through that module's own `api/hooks`
	//      (the "service" tier). They must NOT touch the `@/shared/api` facade,
	//      generated services, or raw fetch/axios directly. A module's own
	//      `api/client` is allowed (sentinel error types live there).
	{
		files: ['src/modules/**/*.{ts,tsx}'],
		ignores: ['**/__tests__/**', '**/*.test.{ts,tsx}'],
		rules: {
			'no-restricted-imports': [
				'error',
				{
					patterns: [
						{
							group: ['../*'],
							message: 'Use @/ absolute imports instead of relative parent paths.',
						},
						{
							group: ['@/shared/ui/*'],
							message:
								'Import shared UI through the barrel: `@/shared/ui` (not a deep path).',
						},
						{
							group: [
								'@/shared/api/*',
								'@/shared/hooks/*',
								'@/shared/lib/*',
								'@/shared/auth/*',
							],
							message:
								'Import shared surfaces through their barrel (`@/shared`), not a deep path.',
						},
					],
				},
			],
		},
	},
	// Layering: components/ + pages/ are the view tier — no direct backend access.
	{
		files: ['src/modules/**/components/**/*.{ts,tsx}', 'src/modules/**/pages/**/*.{ts,tsx}'],
		ignores: ['**/__tests__/**', '**/*.test.{ts,tsx}'],
		rules: {
			'no-restricted-imports': [
				'error',
				{
					paths: [
						{
							name: 'axios',
							message:
								'Views must not call the network directly — go through this module’s api/hooks.',
						},
					],
					patterns: [
						{
							group: ['../*'],
							message: 'Use @/ absolute imports instead of relative parent paths.',
						},
						{
							group: ['@/shared/ui/*'],
							message:
								'Import shared UI through the barrel: `@/shared/ui` (not a deep path).',
						},
						{
							group: ['@/shared/api', '@/shared/api/*'],
							message:
								'Views must not import the @/shared/api facade or generated services — call this module’s api/hooks instead.',
						},
					],
				},
			],
			'no-restricted-globals': [
				'error',
				{
					name: 'fetch',
					message:
						'Views must not call fetch() directly — go through this module’s api/hooks.',
				},
			],
		},
	},

	// ─── Cross-module query-key boundary (#511) ──────────────────────────
	// Per-module: forbid hand-writing a SIBLING module's query-key root as an
	// array literal (carries the #510 /app rule forward too, since these
	// blocks override `no-restricted-syntax` for module files).
	...crossModuleKeyOverrides(),

	// ─── Test files — relaxed rules ──────────────────────────────────────
	{
		files: ['**/__tests__/**', '**/*.test.{ts,tsx}', 'e2e/**/*.{ts,tsx}'],
		languageOptions: {
			globals: { ...globals.browser, ...globals.node },
		},
		rules: {
			'no-console': 'off',
			'no-restricted-imports': 'off',
			'no-restricted-syntax': 'off',
			'import-x/no-restricted-paths': 'off',
			'@typescript-eslint/no-explicit-any': 'off',
			'@typescript-eslint/consistent-type-imports': 'off',
			'react/function-component-definition': 'off',
			'react/button-has-type': 'off',
		},
	},

	// ─── Build scripts (scripts/**) — Node environment ───────────────────
	// One-off Node build/codegen scripts (e.g. gen-favicons.mjs) run under
	// Node, not the browser, and legitimately use console/process. Give them
	// the Node globals and allow console output (they're CLIs, not app code).
	{
		files: ['scripts/**/*.{js,mjs,cjs}'],
		languageOptions: {
			globals: { ...globals.node },
		},
		rules: {
			'no-console': 'off',
		},
	},

	// Prettier must be last to override formatting rules
	eslintPluginPrettierRecommended,

	globalIgnores([
		'dist/',
		'node_modules/',
		'coverage/',
		// Codegen output — vendored, not hand-edited (regenerate via `npm run codegen`).
		'src/shared/api/generated/',
		'playwright-report/',
		'test-results/',
		'public/mockServiceWorker.js',
		'eslint.config.js',
		'vite.config.ts',
		'vitest.config.ts',
		'playwright.config.ts',
		'playwright.docker.config.ts',
		'prettier.config.js',
	]),
);
