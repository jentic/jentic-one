import { http, HttpResponse, delay } from 'msw';
import axe from 'axe-core';
import { screen, waitFor, renderWithProviders, userEvent, createErrorHandler } from '../test-utils';
import { worker } from '../mocks/browser';
import ToolkitDetailPage from '@/pages/ToolkitDetailPage';

function renderToolkit(id = 'test-tk') {
	return renderWithProviders(<ToolkitDetailPage />, {
		route: `/toolkits/${id}`,
		path: '/toolkits/:id',
	});
}

describe('ToolkitDetailPage — read states', () => {
	it('renders toolkit name and description', async () => {
		renderToolkit();

		expect(await screen.findByText('Test Toolkit')).toBeInTheDocument();
		expect(screen.getByText('A test toolkit')).toBeInTheDocument();
	});

	it('shows loading state before data arrives', async () => {
		worker.use(
			http.get('/toolkits/:id', async () => {
				await delay(300);
				return HttpResponse.json({
					id: 'test-tk',
					name: 'Test Toolkit',
					description: 'A test toolkit',
					disabled: false,
					credentials: [],
				});
			}),
		);

		renderToolkit();
		expect(screen.getByTestId('toolkit-loading')).toBeInTheDocument();
		expect(await screen.findByText('Test Toolkit')).toBeInTheDocument();
	});

	it('shows "Toolkit not found" when API returns 404', async () => {
		worker.use(http.get('/toolkits/:id', () => HttpResponse.json(null, { status: 404 })));

		renderToolkit();
		expect(await screen.findByText(/toolkit not found/i)).toBeInTheDocument();
		expect(screen.getByRole('button', { name: /back/i })).toBeInTheDocument();
	});

	it('shows empty keys message when no keys exist', async () => {
		renderToolkit();

		await screen.findByText('Test Toolkit');
		expect(screen.getByText(/no keys yet/i)).toBeInTheDocument();
	});

	it('renders keys when they exist', async () => {
		worker.use(
			http.get('/toolkits/:id/keys', () =>
				HttpResponse.json({
					keys: [
						{
							id: 'k1',
							label: 'Production Key',
							prefix: 'jntc_abc',
							created_at: 1700000000,
						},
					],
				}),
			),
		);

		renderToolkit();
		expect(await screen.findByText('Production Key')).toBeInTheDocument();
		expect(screen.getByText('jntc_abc...')).toBeInTheDocument();
	});

	it('renders credentials section', async () => {
		worker.use(
			http.get('/toolkits/:id', () =>
				HttpResponse.json({
					id: 'test-tk',
					name: 'Test Toolkit',
					description: 'desc',
					disabled: false,
					credentials: [
						{ credential_id: 'c1', label: 'Stripe Token', api_id: 'stripe.com' },
					],
				}),
			),
		);

		renderToolkit();
		expect(await screen.findByText('Stripe Token')).toBeInTheDocument();
		expect(screen.getByText('stripe.com')).toBeInTheDocument();
	});

	it('shows pending requests badge', async () => {
		worker.use(
			http.get('/toolkits/:id/access-requests', () =>
				HttpResponse.json([
					{ id: 'req1', status: 'pending', type: 'grant', reason: 'Need access' },
				]),
			),
		);

		renderToolkit();
		expect(await screen.findByText(/pending access request/i)).toBeInTheDocument();
	});

	it('handles API error gracefully', async () => {
		worker.use(http.get('/toolkits/:id', () => HttpResponse.error()));

		renderToolkit();
		expect(await screen.findByText(/toolkit not found/i)).toBeInTheDocument();
	});

	it('hides Edit button for the default toolkit', async () => {
		worker.use(
			http.get('/toolkits/:id', () =>
				HttpResponse.json({
					id: 'default',
					name: 'Default Toolkit',
					description: 'The default',
					disabled: false,
					credentials: [],
				}),
			),
		);

		renderToolkit('default');
		await screen.findByText('Default Toolkit');
		expect(screen.queryByRole('button', { name: /^edit$/i })).not.toBeInTheDocument();
	});

	it('has no critical accessibility violations', async () => {
		const { container } = renderToolkit();
		await screen.findByText('Test Toolkit');
		const results = await axe.run(container);
		const critical = results.violations.filter(
			(v) => v.impact === 'critical' || v.impact === 'serious',
		);
		expect(critical).toEqual([]);
	});
});

describe('ToolkitDetailPage — create key flow', () => {
	it('opens key creation form and generates a key', async () => {
		const user = userEvent.setup();

		renderToolkit();
		await screen.findByText('Test Toolkit');

		await user.click(screen.getByRole('button', { name: /create key/i }));
		expect(screen.getByText(/create api key/i)).toBeInTheDocument();

		const input = screen.getByPlaceholderText(/key name/i);
		await user.type(input, 'My Key');
		await user.click(screen.getByRole('button', { name: /generate/i }));

		expect(await screen.findByText(/new api key created/i)).toBeInTheDocument();
	});

	it('shows "Generating..." while key is being created', async () => {
		const user = userEvent.setup();

		worker.use(
			http.post('/toolkits/:id/keys', async () => {
				await delay(500);
				return HttpResponse.json({
					id: 'k-new',
					key: 'jntc_new',
					prefix: 'jntc_',
					label: 'Test',
				});
			}),
		);

		renderToolkit();
		await screen.findByText('Test Toolkit');

		await user.click(screen.getByRole('button', { name: /create key/i }));
		await user.click(screen.getByRole('button', { name: /generate/i }));

		expect(await screen.findByRole('button', { name: /generating/i })).toBeDisabled();
	});
});

describe('ToolkitDetailPage — revoke key flow', () => {
	it('shows revoke confirmation and revokes the key', async () => {
		const user = userEvent.setup();

		worker.use(
			http.get('/toolkits/:id/keys', () =>
				HttpResponse.json({
					keys: [
						{ id: 'k1', label: 'Old Key', prefix: 'jntc_old', created_at: 1700000000 },
					],
				}),
			),
		);

		renderToolkit();
		expect(await screen.findByText('Old Key')).toBeInTheDocument();

		const revokeButton = screen.getByRole('button', { name: /^revoke$/i });
		await user.click(revokeButton);

		expect(screen.getByText(/revoke this key/i)).toBeInTheDocument();

		const confirmButton = screen
			.getAllByRole('button', { name: /revoke/i })
			.find((btn) => btn.textContent?.trim() === 'Revoke')!;
		await user.click(confirmButton);

		await waitFor(() => {
			expect(screen.queryByText(/revoke this key/i)).not.toBeInTheDocument();
		});
	});
});

describe('ToolkitDetailPage — kill switch', () => {
	it('shows kill switch confirmation and suspends the toolkit', async () => {
		const user = userEvent.setup();
		let patched = false;

		worker.use(
			http.patch('/toolkits/:id', async ({ request }) => {
				const body = (await request.json()) as Record<string, unknown>;
				patched = true;
				return HttpResponse.json({
					id: 'test-tk',
					name: 'Test Toolkit',
					description: 'A test toolkit',
					disabled: body.disabled,
					credentials: [],
				});
			}),
		);

		renderToolkit();
		await screen.findByText('Test Toolkit');

		await user.click(screen.getByRole('button', { name: /suspend toolkit/i }));
		expect(screen.getByText(/block keys \+ agents/i)).toBeInTheDocument();

		const killBtn = screen
			.getAllByRole('button')
			.find((btn) => btn.textContent?.trim() === 'Kill')!;
		await user.click(killBtn);

		await waitFor(() => expect(patched).toBe(true));
	});
});

describe('ToolkitDetailPage — unbind credential', () => {
	it('unbinds a credential via ConfirmInline', async () => {
		const user = userEvent.setup();
		let unbound = false;

		worker.use(
			http.get('/toolkits/:id', () =>
				HttpResponse.json({
					id: 'test-tk',
					name: 'Test Toolkit',
					description: 'desc',
					disabled: false,
					credentials: [
						{ credential_id: 'c1', label: 'Stripe Token', api_id: 'stripe.com' },
					],
				}),
			),
			http.delete('/toolkits/:id/credentials/:credId', () => {
				unbound = true;
				return new HttpResponse(null, { status: 204 });
			}),
		);

		const { queryClient } = renderToolkit();
		const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
		expect(await screen.findByText('Stripe Token')).toBeInTheDocument();

		await user.click(screen.getByRole('button', { name: /unbind/i }));
		expect(screen.getByText(/unbind this credential/i)).toBeInTheDocument();

		const confirmBtn = screen
			.getAllByRole('button', { name: /unbind/i })
			.find((btn) => btn.textContent?.trim() === 'Unbind')!;
		await user.click(confirmBtn);

		await waitFor(() => expect(unbound).toBe(true));

		// Cross-surface invalidation: the host list / workspace / enrichment counts
		// must refresh so an unbind never leaves a stale "Used by N" chip behind.
		const invalidatedKeys = invalidateSpy.mock.calls.map(
			(c) => (c[0] as { queryKey?: unknown[] })?.queryKey?.[0],
		);
		expect(invalidatedKeys).toEqual(
			expect.arrayContaining(['toolkit', 'toolkits', 'toolkit-card-enrichment', 'workspace']),
		);
	});
});

describe('ToolkitDetailPage — credential permissions (no duplication)', () => {
	const SYSTEM_RULES = [
		{
			effect: 'deny',
			path: 'admin|pay|billing|webhook|secret|token',
			_system: true,
			_comment: 'Deny requests to sensitive path segments',
		},
		{
			effect: 'deny',
			methods: ['POST', 'PUT', 'PATCH', 'DELETE'],
			_system: true,
			_comment: 'Deny write methods by default',
		},
		{ effect: 'allow', _system: true, _comment: 'Allow everything else' },
	];

	function mockToolkitWithCredential() {
		worker.use(
			http.get('/toolkits/:id', () =>
				HttpResponse.json({
					id: 'test-tk',
					name: 'Test Toolkit',
					description: 'desc',
					disabled: false,
					credentials: [
						{ credential_id: 'c1', label: 'Stripe Token', api_id: 'stripe.com' },
					],
				}),
			),
		);
	}

	async function openPermissionEditor(user: ReturnType<typeof userEvent.setup>) {
		await user.click(screen.getByRole('button', { name: /permissions/i }));
		await screen.findByText(/permission rules for/i);
	}

	it('loads only agent rules into the editor — system rules are excluded', async () => {
		const user = userEvent.setup();
		mockToolkitWithCredential();
		worker.use(
			// GET returns one agent rule + the three appended system rules.
			http.get('/toolkits/:id/credentials/:credId/permissions', () =>
				HttpResponse.json([
					{ effect: 'allow', methods: ['GET'], path: '^/v1/charges$' },
					...SYSTEM_RULES,
				]),
			),
		);

		renderToolkit();
		await screen.findByText('Stripe Token');
		await openPermissionEditor(user);

		// Only the single agent rule should be present as an editable row.
		// Each rule row has an "Allow/Deny" select; system rules must NOT
		// be loaded (otherwise saving re-persists them → duplication).
		const selects = await screen.findAllByRole('combobox');
		expect(selects).toHaveLength(1);
		expect(selects[0]).toHaveValue('allow');
	});

	it('does NOT send system rules back on save (prevents duplication)', async () => {
		const user = userEvent.setup();
		let savedBody: any[] | null = null;
		mockToolkitWithCredential();
		worker.use(
			http.get('/toolkits/:id/credentials/:credId/permissions', () =>
				HttpResponse.json([
					{ effect: 'allow', methods: ['GET'], path: '^/v1/charges$' },
					...SYSTEM_RULES,
				]),
			),
			http.put('/toolkits/:id/credentials/:credId/permissions', async ({ request }) => {
				savedBody = (await request.json()) as any[];
				return HttpResponse.json([...(savedBody ?? []), ...SYSTEM_RULES]);
			}),
		);

		renderToolkit();
		await screen.findByText('Stripe Token');
		await openPermissionEditor(user);

		await user.click(screen.getByRole('button', { name: /save rules/i }));

		await waitFor(() => expect(savedBody).not.toBeNull());
		// Exactly the one agent rule round-trips — no system rules, no dupes.
		expect(savedBody).toHaveLength(1);
		expect(savedBody!.every((r) => !('_system' in r))).toBe(true);
		expect(savedBody![0]).toMatchObject({
			effect: 'allow',
			methods: ['GET'],
			path: '^/v1/charges$',
		});
	});

	it('strips empty methods/path and read-only fields from the saved payload', async () => {
		const user = userEvent.setup();
		let savedBody: any[] | null = null;
		mockToolkitWithCredential();
		worker.use(
			// An agent rule with empty path + empty methods (as the editor
			// produces for a freshly-added blank rule).
			http.get('/toolkits/:id/credentials/:credId/permissions', () =>
				HttpResponse.json([{ effect: 'deny', path: '', methods: [] }, ...SYSTEM_RULES]),
			),
			http.put('/toolkits/:id/credentials/:credId/permissions', async ({ request }) => {
				savedBody = (await request.json()) as any[];
				return HttpResponse.json([...(savedBody ?? []), ...SYSTEM_RULES]);
			}),
		);

		renderToolkit();
		await screen.findByText('Stripe Token');
		await openPermissionEditor(user);

		await user.click(screen.getByRole('button', { name: /save rules/i }));

		await waitFor(() => expect(savedBody).not.toBeNull());
		expect(savedBody).toHaveLength(1);
		// Empty path/methods are dropped — only `effect` survives.
		expect(savedBody![0]).toEqual({ effect: 'deny' });
	});

	it('keeps the editor open when the save fails (surfaces the error)', async () => {
		const user = userEvent.setup();
		mockToolkitWithCredential();
		worker.use(
			http.get('/toolkits/:id/credentials/:credId/permissions', () =>
				HttpResponse.json([
					{ effect: 'allow', methods: ['GET'], path: '^/v1/charges$' },
					...SYSTEM_RULES,
				]),
			),
			createErrorHandler('put', '/toolkits/:id/credentials/:credId/permissions', {
				status: 500,
			}),
		);

		renderToolkit();
		await screen.findByText('Stripe Token');
		await openPermissionEditor(user);

		await user.click(screen.getByRole('button', { name: /save rules/i }));

		// onError must NOT close the editor (only onSuccess does). The user
		// keeps their in-progress rules and sees the failure.
		await waitFor(() =>
			expect(screen.getByRole('button', { name: /save rules/i })).toBeEnabled(),
		);
		expect(screen.getByText(/permission rules for/i)).toBeInTheDocument();
	});

	it('repeated open→save cycles never grow the agent rule list', async () => {
		const user = userEvent.setup();
		const putBodies: any[][] = [];
		mockToolkitWithCredential();

		// Simulate the server: stored agent rules start with one entry, and
		// GET always returns stored + system rules. If the bug regressed,
		// the editor would re-save system rules and the list would grow.
		let stored: any[] = [{ effect: 'allow', methods: ['GET'], path: '^/v1/charges$' }];
		worker.use(
			http.get('/toolkits/:id/credentials/:credId/permissions', () =>
				HttpResponse.json([...stored, ...SYSTEM_RULES]),
			),
			http.put('/toolkits/:id/credentials/:credId/permissions', async ({ request }) => {
				const body = (await request.json()) as any[];
				putBodies.push(body);
				stored = body; // server persists ONLY what the client sent
				return HttpResponse.json([...stored, ...SYSTEM_RULES]);
			}),
		);

		renderToolkit();
		await screen.findByText('Stripe Token');

		// First cycle
		await openPermissionEditor(user);
		await user.click(screen.getByRole('button', { name: /save rules/i }));
		await waitFor(() => expect(putBodies).toHaveLength(1));

		// Second cycle — reopen and save again
		await waitFor(() =>
			expect(screen.queryByText(/permission rules for/i)).not.toBeInTheDocument(),
		);
		await openPermissionEditor(user);
		await user.click(screen.getByRole('button', { name: /save rules/i }));
		await waitFor(() => expect(putBodies).toHaveLength(2));

		// Both saves persisted exactly one agent rule — no growth.
		expect(putBodies[0]).toHaveLength(1);
		expect(putBodies[1]).toHaveLength(1);
		expect(stored).toHaveLength(1);
	});
});

describe('ToolkitDetailPage — mutation errors', () => {
	it('reverts kill switch toggle on server error', async () => {
		const user = userEvent.setup();

		worker.use(createErrorHandler('patch', '/toolkits/:id', { status: 500 }));

		renderToolkit();
		await screen.findByText('Test Toolkit');

		await user.click(screen.getByRole('button', { name: /suspend toolkit/i }));
		expect(screen.getByText(/block keys \+ agents/i)).toBeInTheDocument();

		const killBtn = screen
			.getAllByRole('button')
			.find((btn) => btn.textContent?.trim() === 'Kill')!;
		await user.click(killBtn);

		await waitFor(() => {
			expect(screen.queryByText(/toolkit suspended/i)).not.toBeInTheDocument();
		});
	});

	it('shows error when creating a key fails with 500', async () => {
		const user = userEvent.setup();

		worker.use(createErrorHandler('post', '/toolkits/:id/keys', { status: 500 }));

		renderToolkit();
		await screen.findByText('Test Toolkit');

		await user.click(screen.getByRole('button', { name: /create key/i }));
		expect(screen.getByText(/create api key/i)).toBeInTheDocument();

		await user.click(screen.getByRole('button', { name: /generate/i }));

		await waitFor(() => {
			expect(screen.queryByText(/new api key created/i)).not.toBeInTheDocument();
		});
	});

	it('does not navigate away when deleting a toolkit fails with 500', async () => {
		const user = userEvent.setup();

		worker.use(createErrorHandler('delete', '/toolkits/:id', { status: 500 }));

		renderToolkit();
		await screen.findByText('Test Toolkit');

		// Delete lives directly in the header now (not behind Settings). Before
		// the dialog opens it's the only "Delete toolkit" control.
		await user.click(screen.getByRole('button', { name: /delete toolkit/i }));

		// The cascade dialog warns before committing.
		expect(await screen.findByText(/will be permanently deleted/i)).toBeInTheDocument();

		// Now both the header button and the dialog's confirm button read
		// "Delete toolkit"; the last match is the one inside the dialog footer.
		const confirmButtons = screen.getAllByRole('button', { name: /^delete toolkit$/i });
		await user.click(confirmButtons[confirmButtons.length - 1]);

		// Still on the page (didn't navigate away on the failed delete). The
		// name now also appears inside the still-open dialog body, so match
		// on "at least one".
		await waitFor(() => {
			expect(screen.getAllByText('Test Toolkit').length).toBeGreaterThan(0);
		});
	});
});
