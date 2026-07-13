/**
 * DocsPage — page-level test (renders in isolation under the app providers).
 *
 * The docs portal is a public, standalone route that fetches four same-origin
 * sources — the OpenAPI document, the canonical scope reference, the build-time
 * CLI reference, and the standalone Broker OpenAPI document — and renders them
 * as one narrative document (Overview → … → API reference → Broker API). MSW
 * isn't seeded with these by default (they live only on the real instance), so
 * each test registers its own handlers.
 *
 * Coverage:
 *  - the narrative hero + section headings render once data resolves;
 *  - the native API reference renders an operation with its scope panel,
 *    enriched from the reference payload (the join the portal exists to show);
 *  - the Broker reference renders as its own section from its own spec;
 *  - a missing reference endpoint degrades to a graceful, retryable notice
 *    instead of a blank route;
 *  - no critical/serious a11y violations on the assembled page.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { act } from '@testing-library/react';
import {
	renderWithProviders,
	screen,
	within,
	checkA11y,
	createErrorHandler,
} from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import DocsPage from '@/modules/docs/pages/DocsPage';
import { operationAnchorId } from '@/modules/docs/lib/anchor';
import type { CliReference, ReferencePayload } from '@/modules/docs/api/types';

const SPEC = {
	openapi: '3.1.0',
	info: { title: 'Jentic One', version: '1.0.0' },
	servers: [{ url: 'https://api.example.com', description: 'Production' }],
	'x-tagGroups': [{ name: 'Core', tags: ['Agents'] }],
	tags: [{ name: 'Agents', description: 'Manage agents.' }],
	paths: {
		'/agents': {
			get: {
				operationId: 'list_agents',
				summary: 'List agents',
				tags: ['Agents'],
				responses: { '200': { description: 'OK' } },
			},
		},
	},
	components: {
		securitySchemes: {
			BearerAuth: { type: 'http', scheme: 'bearer' },
		},
		schemas: {
			Agent: {
				type: 'object',
				description: 'A registered agent.',
				properties: { id: { type: 'string' } },
			},
		},
	},
};

const REFERENCE: ReferencePayload = {
	schema: 'jentic.endpoint-scope-tree/v1',
	total: 1,
	groups: ['Agents'],
	endpoints: [
		{
			method: 'GET',
			path: '/agents',
			surface: 'control',
			summary: 'List agents',
			operation_id: 'list_agents',
			authenticated: true,
			public: false,
			actor_types: ['user'],
			required_scopes: ['agents:read'],
			implied_scopes: {},
			auth_note: null,
			typical_caller: 'operator',
			group: 'Agents',
		},
	],
};

const CLI: CliReference = {
	schema: 'jentic.cli-reference/v1',
	binaries: [
		{
			name: 'jentic',
			short: 'The Jentic agent CLI.',
			commands: [
				{
					name: 'login',
					path: 'jentic login',
					use: 'jentic login',
					short: 'Authenticate the CLI.',
				},
			],
		},
	],
};

/**
 * The Broker spec is a separate, standalone document (its own server, its own
 * proxy operation + models). The portal renders it as its own reference section
 * with namespaced anchors, so the fixture mirrors that distinct shape.
 */
const BROKER_SPEC = {
	openapi: '3.1.0',
	info: { title: 'Broker API', version: 'beta.1' },
	servers: [{ url: 'https://broker.example.com', description: 'Production' }],
	tags: [{ name: 'Executions', description: 'Proxy upstream API operations.' }],
	security: [{ BearerAuth: [] }],
	paths: {
		'/{upstream_url}': {
			post: {
				operationId: 'executePost',
				summary: 'Execute a POST against an upstream API',
				tags: ['Executions'],
				responses: { '200': { description: 'Synchronous success.' } },
			},
		},
	},
	components: {
		securitySchemes: { BearerAuth: { type: 'http', scheme: 'bearer', bearerFormat: 'JWT' } },
		schemas: {
			AsyncQueuedResponse: {
				type: 'object',
				description: 'Handle returned when the Broker queues an async call.',
				properties: { job_id: { type: 'string' } },
			},
		},
	},
};

/** Register the docs sources MSW doesn't carry by default. */
function seedDocsHandlers(overrides: { reference?: unknown } = {}) {
	worker.use(
		http.get('/openapi.json', () => HttpResponse.json(SPEC)),
		http.get('/reference/endpoints.json', () =>
			HttpResponse.json((overrides.reference ?? REFERENCE) as object),
		),
		// Resolved relative to document.baseURI in the client; match by suffix.
		http.get('*/cli-reference.json', () => HttpResponse.json(CLI)),
		http.get('*/broker-openapi.json', () => HttpResponse.json(BROKER_SPEC)),
	);
}

describe('DocsPage', () => {
	beforeEach(() => {
		seedDocsHandlers();
	});

	it('renders the narrative hero and the section headings', async () => {
		renderWithProviders(<DocsPage />);

		expect(
			await screen.findByText('Secure third-party API execution for AI agents.'),
		).toBeInTheDocument();
		expect(screen.getByRole('heading', { name: 'Overview' })).toBeInTheDocument();
		expect(screen.getByRole('heading', { name: 'CLI' })).toBeInTheDocument();
		expect(screen.getByRole('heading', { name: 'API reference' })).toBeInTheDocument();
	});

	it('renders an API operation enriched with its required scope', async () => {
		renderWithProviders(<DocsPage />);
		// The reference's operation path appears in the left index once parsed.
		await screen.findByText('Secure third-party API execution for AI agents.');

		// The operation block is lazily mounted far down the page; scroll its
		// anchor into view so the IntersectionObserver mounts it.
		await act(async () => {
			document.getElementById(operationAnchorId('GET', '/agents'))?.scrollIntoView();
		});

		expect(await screen.findByText('List agents', {}, { timeout: 3000 })).toBeInTheDocument();
		// The scope reference is the join the portal exists to surface — it shows
		// in both the operation's auth chip and its scope panel.
		const scopeTokens = await screen.findAllByText('agents:read');
		expect(scopeTokens.length).toBeGreaterThan(0);
	});

	it('renders the Broker reference as its own section from its own spec', async () => {
		renderWithProviders(<DocsPage />);
		await screen.findByText('Secure third-party API execution for AI agents.');

		// The Broker is a distinct section with its own heading.
		expect(screen.getByRole('heading', { name: 'Broker API' })).toBeInTheDocument();

		// Its operation is lazily mounted under a namespaced anchor so it never
		// collides with the control-plane reference; scroll it into view to mount.
		const brokerOpId = `broker-${operationAnchorId('POST', '/{upstream_url}')}`;
		await act(async () => {
			document.getElementById(brokerOpId)?.scrollIntoView();
		});

		expect(
			await screen.findByText(
				'Execute a POST against an upstream API',
				{},
				{ timeout: 3000 },
			),
		).toBeInTheDocument();
	});

	it('degrades gracefully when the reference endpoint is missing', async () => {
		worker.use(createErrorHandler('get', '/reference/endpoints.json', { status: 404 }));
		renderWithProviders(<DocsPage />);

		const alert = await screen.findByRole('alert');
		expect(within(alert).getByText(/endpoint reference/i)).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
	});

	it('does not crash on a malformed-but-200 reference payload', async () => {
		// required_scopes / implied_scopes omitted: the renderer must normalize
		// these to empty rather than throwing and blanking the route.
		seedDocsHandlers({
			reference: {
				schema: 'jentic.endpoint-scope-tree/v1',
				total: 1,
				groups: ['Agents'],
				endpoints: [
					{
						method: 'GET',
						path: '/agents',
						surface: 'control',
						summary: 'List agents',
						operation_id: 'list_agents',
						authenticated: true,
						public: false,
						group: 'Agents',
					},
				],
			},
		});
		renderWithProviders(<DocsPage />);
		await screen.findByText('Secure third-party API execution for AI agents.');

		await act(async () => {
			document.getElementById(operationAnchorId('GET', '/agents'))?.scrollIntoView();
		});

		// The page still renders the operation; the scope panel shows the
		// "no specific scope" fallback instead of crashing.
		expect(await screen.findByText('List agents', {}, { timeout: 3000 })).toBeInTheDocument();
		expect(await screen.findByText(/no specific scope/i)).toBeInTheDocument();
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderWithProviders(<DocsPage />);
		await screen.findByText('Secure third-party API execution for AI agents.');
		await checkA11y(container);
	});
});
