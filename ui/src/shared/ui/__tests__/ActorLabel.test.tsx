import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, it, expect } from 'vitest';
import { http, HttpResponse } from 'msw';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { worker } from '@/mocks/browser';
import { setToken, clearToken, ActorType } from '@/shared/api';
import { ActorLabel } from '@/shared/ui/ActorLabel';

function wrapper({ children }: { children: ReactNode }) {
	const queryClient = new QueryClient({
		defaultOptions: { queries: { retry: false, gcTime: 0 } },
	});
	return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

function seedActors() {
	worker.use(
		http.get('/actors', () =>
			HttpResponse.json({
				data: [
					{
						id: 'agnt_known',
						name: 'Inbox Triage',
						actor_type: 'agent',
						active: true,
						created_at: '2026-01-01T00:00:00Z',
					},
				],
				has_more: false,
				next_cursor: null,
			}),
		),
	);
}

describe('ActorLabel', () => {
	beforeEach(() => setToken('mock-access-token'));
	afterEach(() => clearToken());

	it('resolves a known id to its name', async () => {
		seedActors();
		render(<ActorLabel actorId="agnt_known" />, { wrapper });
		expect(await screen.findByText('Inbox Triage')).toBeInTheDocument();
	});

	it('keeps the raw id reachable on hover via title', async () => {
		seedActors();
		render(<ActorLabel actorId="agnt_known" />, { wrapper });
		const el = await screen.findByText('Inbox Triage');
		expect(el).toHaveAttribute('title', 'agnt_known');
	});

	it('falls back to the raw id (mono) for an unknown id', async () => {
		seedActors();
		render(<ActorLabel actorId="agnt_unknown" />, { wrapper });
		const raw = await screen.findByText('agnt_unknown');
		expect(raw).toBeInTheDocument();
		expect(raw.className).toContain('font-mono');
	});

	it('shows a subtle type prefix when resolved with an actor type', async () => {
		seedActors();
		render(<ActorLabel actorId="agnt_known" actorType={ActorType.AGENT} />, { wrapper });
		await waitFor(() => expect(screen.getByText('Inbox Triage')).toBeInTheDocument());
		expect(screen.getByText(/Agent/)).toBeInTheDocument();
	});

	// Toolkits are never in the actor directory (the backend UNION excludes them),
	// but they DO surface as the actor of broker-path executions/audit entries with
	// `actor_type === "toolkit"`. The id never resolves, so it must fall back to the
	// raw `tk_…` token while still labelling it as a toolkit.
	it('labels a toolkit actor and keeps its raw id (directory never holds toolkits)', async () => {
		seedActors();
		render(<ActorLabel actorId="tk_acme" actorType={ActorType.TOOLKIT} />, { wrapper });
		const raw = await screen.findByText('tk_acme');
		expect(raw).toBeInTheDocument();
		expect(raw.className).toContain('font-mono');
		expect(screen.getByText(/Toolkit/)).toBeInTheDocument();
	});

	// `registered_by: "self"` is a backend sentinel (agent self-registered via DCR),
	// not a KSUID. It must render as a plain word, never as a mono id-looking token.
	it('renders the "self" sentinel as a plain word, not a mono id', async () => {
		seedActors();
		render(<ActorLabel actorId="self" />, { wrapper });
		const el = await screen.findByText('Self');
		expect(el).toBeInTheDocument();
		expect(el.className).not.toContain('font-mono');
		expect(screen.queryByText('self')).not.toBeInTheDocument();
	});
});
