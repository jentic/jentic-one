import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { http, HttpResponse } from 'msw';
import { worker } from '@/mocks/browser';
import {
	CredentialType,
	DEVICE_CODE_CONNECT_TIMEOUT_MS,
	OAUTH_CONNECT_MESSAGE_TYPE,
	POPUP_CONNECT_TIMEOUT_MS,
	runConnectFlow,
	type CredentialRedactedResponse,
} from '@/shared/credentials/api';
import {
	makeMockCredential,
	resetCredentialsStore,
	setConnectAutoCompletes,
} from '@/shared/credentials/mocks/handlers';

function stubFlowStatus(connected: () => boolean): void {
	worker.use(
		http.get('/credentials/:id/connect/:flowId/status', () =>
			HttpResponse.json({ status: connected() ? 'connected' : 'pending' }),
		),
	);
}

/**
 * `runConnectFlow` is the single opener-side chokepoint for the OAuth connect
 * round-trip. It learns the outcome by polling the exact flow status and then
 * reading the connected credential; #598 adds an *advisory* `postMessage` from
 * the `/oauth/connected` popup page so the opener can re-read immediately
 * instead of waiting for the next poll tick. These tests pin the message
 * contract: the opener re-reads on a valid, same-origin message, ignores
 * foreign-origin messages, and never trusts the message payload as the source
 * of truth (the credentials API stays canonical).
 */
describe('runConnectFlow — advisory postMessage (#598)', () => {
	let fakePopup: { closed: boolean; close: () => void };

	beforeEach(() => {
		resetCredentialsStore();
		// We drive the "connected" transition by hand so the message — not the
		// mock's auto-complete timer — is what unblocks the wait.
		setConnectAutoCompletes(false);
		fakePopup = { closed: false, close: vi.fn() };
		vi.spyOn(window, 'open').mockReturnValue(fakePopup as unknown as Window);
	});

	afterEach(() => {
		vi.restoreAllMocks();
		resetCredentialsStore();
		setConnectAutoCompletes(true);
	});

	function seedConnectableCredential(): string {
		const cred = makeMockCredential({ type: CredentialType.OAUTH2, provider: 'direct_oauth2' });
		return cred.credential_id;
	}

	it('re-reads and resolves connected when a same-origin advisory message arrives', async () => {
		const id = seedConnectableCredential();
		// The "callback landed" effect: once the popup signals, the credential is
		// connected. We flip the mock to report a connection on the next read.
		let connected = false;
		stubFlowStatus(() => connected);
		let reads = 0;
		worker.use(
			http.get('/credentials/:id', ({ params }) => {
				if (String(params.id) !== id) return undefined;
				reads += 1;
				return HttpResponse.json({
					credential_id: id,
					provider_account_ref: connected ? 'connected' : null,
					details: { connected },
					updated_at: connected ? new Date().toISOString() : null,
				} as Partial<CredentialRedactedResponse>);
			}),
		);

		// Start the flow with a long poll interval — if the message short-circuit
		// is broken, the test would hang past its timeout rather than resolve fast.
		const flow = runConnectFlow(id, { pollMs: 60_000, timeoutMs: 120_000 });

		// Simulate the popup completing then posting its advisory signal before
		// the first scheduled poll. The listener is attached before the connect
		// POST, and one message wakes the 60 s poll wait.
		await vi.waitFor(() => expect(window.open).toHaveBeenCalled());
		connected = true;
		window.dispatchEvent(
			new MessageEvent('message', {
				origin: window.location.origin,
				data: { type: OAUTH_CONNECT_MESSAGE_TYPE, status: 'ok' },
			}),
		);

		const outcome = await flow;
		expect(outcome.status).toBe('connected');
		expect(reads).toBe(1);
	});

	it('does not treat a rename of an already-connected credential as this flow completing', async () => {
		const id = seedConnectableCredential();
		let reads = 0;
		stubFlowStatus(() => reads >= 3);
		worker.use(
			http.get('/credentials/:id', ({ params }) => {
				if (String(params.id) !== id) return undefined;
				reads += 1;
				return HttpResponse.json({
					credential_id: id,
					provider_account_ref: null,
					details: { connected: true },
					updated_at: reads === 1 ? '2026-01-01T00:00:00Z' : '2026-01-01T00:01:00Z',
				} as Partial<CredentialRedactedResponse>);
			}),
		);

		const outcome = await runConnectFlow(id, { pollMs: 25, timeoutMs: 2000 });
		expect(outcome.status).toBe('connected');
		expect(reads).toBe(3);
	});

	it("does not complete one challenge from another challenge's connected status", async () => {
		const id = seedConnectableCredential();
		const requestedFlows: string[] = [];
		worker.use(
			http.get('/credentials/:id', () =>
				HttpResponse.json({
					credential_id: id,
					details: { connected: true },
				} as Partial<CredentialRedactedResponse>),
			),
			http.get('/credentials/:id/connect/:flowId/status', ({ params }) => {
				const flowId = String(params.flowId);
				requestedFlows.push(flowId);
				return HttpResponse.json({
					status: flowId === 'other-flow' ? 'connected' : 'pending',
				});
			}),
		);
		fakePopup.closed = true;
		const outcome = await runConnectFlow(id, { pollMs: 10, timeoutMs: 1000 });
		expect(outcome.status).toBe('cancelled');
		expect(requestedFlows.length).toBeGreaterThan(0);
		expect(new Set(requestedFlows).size).toBe(1);
		expect(requestedFlows[0]).not.toBe('other-flow');
	});

	it('honors abort after a deferred popup status read', async () => {
		const id = seedConnectableCredential();
		const controller = new AbortController();
		let release!: () => void;
		const pending = new Promise<void>((resolve) => {
			release = resolve;
		});
		let requested = 0;
		worker.use(
			http.get('/credentials/:id/connect/:flowId/status', async () => {
				requested += 1;
				await pending;
				return HttpResponse.json({ status: 'connected' });
			}),
			http.get('/credentials/:id', () =>
				HttpResponse.json({ credential_id: id, details: { connected: true } }),
			),
		);
		const flow = runConnectFlow(id, { pollMs: 1, signal: controller.signal });
		await vi.waitFor(() => expect(requested).toBe(1));
		controller.abort();
		release();
		expect(await flow).toEqual({ status: 'cancelled' });
		expect(fakePopup.close).toHaveBeenCalled();
	});

	it('honors abort after a deferred begin-connect response', async () => {
		const id = seedConnectableCredential();
		const controller = new AbortController();
		let release!: () => void;
		const pending = new Promise<void>((resolve) => {
			release = resolve;
		});
		let begins = 0;
		worker.use(
			http.post('/credentials/:id/connect', async () => {
				begins += 1;
				await pending;
				return HttpResponse.json({
					kind: 'authorization_code',
					flow_id: 'deferred-flow',
					authorize_url: 'https://vendor.example.test/oauth/authorize',
				});
			}),
		);
		const flow = runConnectFlow(id, { pollMs: 1, signal: controller.signal });
		await vi.waitFor(() => expect(begins).toBe(1));
		controller.abort();
		release();
		expect(await flow).toEqual({ status: 'cancelled' });
		expect(window.open).not.toHaveBeenCalled();
	});

	it('honors abort after the popup-final status read', async () => {
		const id = seedConnectableCredential();
		const controller = new AbortController();
		let release!: () => void;
		const pending = new Promise<void>((resolve) => {
			release = resolve;
		});
		let reads = 0;
		worker.use(
			http.get('/credentials/:id', () =>
				HttpResponse.json({ credential_id: id, details: { connected: true } }),
			),
			http.get('/credentials/:id/connect/:flowId/status', async () => {
				reads += 1;
				if (reads === 2) {
					fakePopup.closed = true;
					await pending;
				}
				return HttpResponse.json({ status: 'pending' });
			}),
		);
		const flow = runConnectFlow(id, { pollMs: 1, signal: controller.signal });
		await vi.waitFor(() => expect(reads).toBe(2));
		controller.abort();
		release();
		expect(await flow).toEqual({ status: 'cancelled' });
		expect(fakePopup.close).toHaveBeenCalled();
	});

	it('ignores a message from a foreign origin (no early resolve)', async () => {
		const id = seedConnectableCredential();
		worker.use(
			http.get('/credentials/:id', ({ params }) => {
				if (String(params.id) !== id) return undefined;
				// Never connects on its own — only an accepted message + flip would.
				return HttpResponse.json({
					credential_id: id,
					provider_account_ref: null,
					updated_at: null,
				} as Partial<CredentialRedactedResponse>);
			}),
		);

		const flow = runConnectFlow(id, { pollMs: 50, timeoutMs: 400 });

		await new Promise((r) => setTimeout(r, 10));
		// Wrong origin → must be ignored. Credential never flips, so the flow can
		// only end via the short timeout, proving the message did not short-circuit
		// it into a (false) connected.
		window.dispatchEvent(
			new MessageEvent('message', {
				origin: 'https://evil.example.com',
				data: { type: OAUTH_CONNECT_MESSAGE_TYPE, status: 'ok' },
			}),
		);

		const outcome = await flow;
		expect(outcome.status).toBe('timeout');
	});

	it('ignores a message whose source is not the popup we opened (no early resolve)', async () => {
		const id = seedConnectableCredential();
		worker.use(
			http.get('/credentials/:id', ({ params }) => {
				if (String(params.id) !== id) return undefined;
				return HttpResponse.json({
					credential_id: id,
					provider_account_ref: null,
					updated_at: null,
				} as Partial<CredentialRedactedResponse>);
			}),
		);

		const flow = runConnectFlow(id, { pollMs: 50, timeoutMs: 400 });

		await new Promise((r) => setTimeout(r, 30));
		// Same origin + correct type, but from an *unrelated* window (not our
		// popup). Hardening (#611 review L1) requires this be ignored, so an
		// unrelated tab/iframe can't pace our poll loop. Credential never flips,
		// so the only way out is the timeout. MessageEvent.source must be a real
		// window, so borrow an iframe's contentWindow as the foreign source.
		const iframe = document.createElement('iframe');
		document.body.appendChild(iframe);
		const otherWindow = iframe.contentWindow as Window;
		window.dispatchEvent(
			new MessageEvent('message', {
				origin: window.location.origin,
				source: otherWindow,
				data: { type: OAUTH_CONNECT_MESSAGE_TYPE, status: 'ok' },
			}),
		);

		const outcome = await flow;
		expect(outcome.status).toBe('timeout');
		iframe.remove();
	});

	it('does not busy-loop after an error message — the signal wakes the loop once, then pacing resumes', async () => {
		const id = seedConnectableCredential();
		// The credential never connects (mirrors an error outcome). Count how many
		// times the loop re-reads while it waits out the timeout.
		let reads = 0;
		worker.use(
			http.get('/credentials/:id', ({ params }) => {
				if (String(params.id) !== id) return undefined;
				reads += 1;
				return HttpResponse.json({
					credential_id: id,
					provider_account_ref: null,
					updated_at: null,
				} as Partial<CredentialRedactedResponse>);
			}),
		);

		// pollMs 50, timeout 600 → a correctly-paced loop reads ~12 times
		// (600/50). A latched busy-loop would fire hundreds of back-to-back
		// requests. We post an error message early to
		// trip the latch, which must not be permanent.
		const flow = runConnectFlow(id, { pollMs: 50, timeoutMs: 600 });

		await new Promise((r) => setTimeout(r, 20));
		window.dispatchEvent(
			new MessageEvent('message', {
				origin: window.location.origin,
				data: { type: OAUTH_CONNECT_MESSAGE_TYPE, status: 'error' },
			}),
		);

		const outcome = await flow;
		expect(outcome.status).toBe('timeout');
		// Generous ceiling: correct pacing is ~12 reads; a busy-loop would be
		// orders of magnitude higher. 40 leaves slack for timing jitter while
		// still failing hard on a regression to the latched behaviour.
		expect(reads).toBeLessThan(40);
	});
});

/**
 * The device-code branch of ``runConnectFlow`` is a distinct path: no
 * popup is ever opened (the human types a ``user_code`` at the vendor's
 * verification URI), and completion is driven by the backend
 * ``ConnectPollScanner`` rather than the OAuth callback route. These
 * tests pin the three load-bearing behaviours:
 *
 *   * a device-code challenge WITHOUT a caller-supplied render hook
 *     returns ``unsupported_challenge`` immediately — polling in silence
 *     would look like a hang from the user's perspective;
 *   * WITH a render hook, the flow polls the credentials API for
 *     completion just like auth-code — no popup, no ``window.open`` at all;
 *   * the cleanup returned from the render hook fires exactly once, on
 *     every terminal outcome (connected / timeout).
 */
describe('runConnectFlow — device-code branch', () => {
	beforeEach(() => {
		resetCredentialsStore();
		setConnectAutoCompletes(false);
	});

	afterEach(() => {
		vi.restoreAllMocks();
		resetCredentialsStore();
		setConnectAutoCompletes(true);
	});

	function seedDeviceCodeCredential(): string {
		const cred = makeMockCredential({
			type: CredentialType.OAUTH2,
			provider: 'device_authorization',
		});
		return cred.credential_id;
	}

	function stubDeviceCodeConnectResponse(id: string): void {
		worker.use(
			http.post('/credentials/:id/connect', ({ params }) => {
				if (String(params.id) !== id) return undefined;
				return HttpResponse.json({
					kind: 'device_authorization',
					flow_id: 'flow-device-1',
					user_code: 'ABCD-1234',
					verification_uri: 'https://idp.example.com/device',
					verification_uri_complete: 'https://idp.example.com/device?user_code=ABCD-1234',
					poll_interval_seconds: 5,
				});
			}),
		);
	}

	it('returns unsupported_challenge when no render hook is provided', async () => {
		const id = seedDeviceCodeCredential();
		stubDeviceCodeConnectResponse(id);
		const windowOpen = vi.spyOn(window, 'open');

		const outcome = await runConnectFlow(id, { pollMs: 20, timeoutMs: 300 });

		expect(outcome.status).toBe('unsupported_challenge');
		// No popup on the device-code path — that's the whole point of the
		// render-hook contract.
		expect(windowOpen).not.toHaveBeenCalled();
	});

	it('invokes the render hook, polls until connected, and never opens a popup', async () => {
		const id = seedDeviceCodeCredential();
		stubDeviceCodeConnectResponse(id);
		const windowOpen = vi.spyOn(window, 'open');

		let connected = false;
		stubFlowStatus(() => connected);
		let reads = 0;
		worker.use(
			http.get('/credentials/:id', ({ params }) => {
				if (String(params.id) !== id) return undefined;
				reads += 1;
				return HttpResponse.json({
					credential_id: id,
					provider_account_ref: connected ? 'connected' : null,
					details: { connected },
					updated_at: connected ? new Date().toISOString() : null,
				} as Partial<CredentialRedactedResponse>);
			}),
		);

		const rendered: Array<{ user_code: string }> = [];
		let cleanupCalls = 0;
		const flow = runConnectFlow(id, {
			pollMs: 50,
			timeoutMs: 3000,
			onDeviceAuthorizationChallenge: (challenge) => {
				rendered.push({ user_code: challenge.user_code });
				return () => {
					cleanupCalls += 1;
				};
			},
		});

		// Simulate the human approving at the vendor and the scanner
		// vaulting the token — the exact flow status flips connected server-side
		// after the first poll read.
		await vi.waitFor(() => expect(reads).toBe(1));
		connected = true;

		const outcome = await flow;

		expect(outcome.status).toBe('connected');
		// The device-code render hook is called exactly once with the
		// user_code, and never at all opens a popup.
		expect(rendered).toEqual([{ user_code: 'ABCD-1234' }]);
		expect(windowOpen).not.toHaveBeenCalled();
		// The cleanup returned from the hook fires on the terminal outcome
		// so the caller can dismiss its modal / dialog.
		expect(cleanupCalls).toBe(1);
	});

	it('aborting the signal cancels the device poll loop (no timeout)', async () => {
		// The device dialog's Cancel button aborts the caller-supplied
		// AbortSignal; the loop must resolve promptly with a ``cancelled``
		// outcome (NOT run out the timeout and report ``timeout``), and the
		// render-hook cleanup must still fire so the modal is dismissed.
		const id = seedDeviceCodeCredential();
		stubDeviceCodeConnectResponse(id);
		worker.use(
			http.get('/credentials/:id', ({ params }) => {
				if (String(params.id) !== id) return undefined;
				return HttpResponse.json({
					credential_id: id,
					provider_account_ref: null,
					updated_at: null,
				} as Partial<CredentialRedactedResponse>);
			}),
		);

		const controller = new AbortController();
		let cleanupCalls = 0;
		const startedAt = Date.now();
		const flow = runConnectFlow(id, {
			pollMs: 60_000, // would run forever without the abort
			timeoutMs: 120_000,
			signal: controller.signal,
			onDeviceAuthorizationChallenge: () => {
				return () => {
					cleanupCalls += 1;
				};
			},
		});

		await new Promise((r) => setTimeout(r, 50));
		controller.abort();

		const outcome = await flow;
		expect(outcome.status).toBe('cancelled');
		// Resolved via the abort, not by waiting out pollMs/timeoutMs.
		expect(Date.now() - startedAt).toBeLessThan(5000);
		expect(cleanupCalls).toBe(1);
	});

	it('honors abort after a deferred device credential read and runs cleanup', async () => {
		const id = seedDeviceCodeCredential();
		stubDeviceCodeConnectResponse(id);
		stubFlowStatus(() => true);
		const controller = new AbortController();
		let release!: () => void;
		const pending = new Promise<void>((resolve) => {
			release = resolve;
		});
		let reads = 0;
		let cleanupCalls = 0;
		worker.use(
			http.get('/credentials/:id', async () => {
				reads += 1;
				if (reads === 1) await pending;
				return HttpResponse.json({ credential_id: id, details: { connected: true } });
			}),
		);
		const flow = runConnectFlow(id, {
			pollMs: 1,
			signal: controller.signal,
			onDeviceAuthorizationChallenge: () => () => {
				cleanupCalls += 1;
			},
		});
		await vi.waitFor(() => expect(reads).toBe(1));
		controller.abort();
		release();
		expect(await flow).toEqual({ status: 'cancelled' });
		expect(cleanupCalls).toBe(1);
	});

	it('runs cleanup on timeout too', async () => {
		const id = seedDeviceCodeCredential();
		stubDeviceCodeConnectResponse(id);
		worker.use(
			http.get('/credentials/:id', ({ params }) => {
				if (String(params.id) !== id) return undefined;
				return HttpResponse.json({
					credential_id: id,
					provider_account_ref: null,
					updated_at: null,
				} as Partial<CredentialRedactedResponse>);
			}),
		);

		let cleanupCalls = 0;
		const outcome = await runConnectFlow(id, {
			pollMs: 30,
			timeoutMs: 200,
			onDeviceAuthorizationChallenge: () => {
				return () => {
					cleanupCalls += 1;
				};
			},
		});

		expect(outcome.status).toBe('timeout');
		// If cleanup ever stops firing on timeout, a stranded modal would
		// linger over the credentials page after the flow expires.
		expect(cleanupCalls).toBe(1);
	});

	it('gives a device code its full lifetime by default, not the popup’s two minutes', () => {
		// The human enters the code on another device; vendors issue codes for
		// about 15 minutes, so the default wait must not discard it sooner (#1428).
		expect(DEVICE_CODE_CONNECT_TIMEOUT_MS).toBe(15 * 60_000);
		expect(DEVICE_CODE_CONNECT_TIMEOUT_MS).toBeGreaterThan(POPUP_CONNECT_TIMEOUT_MS);
	});
});
