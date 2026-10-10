/**
 * The "Can call" list re-renders only the rows whose own props change: pinning
 * one row (a state change of the whole panel) re-renders that row, not its
 * siblings. Read off React's committed tree: a row its memo lets through gets
 * a new props object; a bailed-out row keeps the one it had.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { page } from 'vitest/browser';
import { renderWithProviders, screen, waitFor, userEvent } from '@/__tests__/test-utils';
import { setToken } from '@/shared/api';
import { resetAgentsStore } from '@/modules/agents/mocks/handlers';
import {
	makeMockCredential,
	resetApisStore,
	resetCredentialsStore,
} from '@/shared/credentials/mocks/handlers';
import { CredentialType, type ApiResponse } from '@/shared/credentials/api';
import { ApiRow } from '@/modules/agents/components/flat/ApiRow';
import AgentsPage from '@/modules/agents/pages/AgentsPage';
import { resetOrphanPurgeAttemptsForTest } from '@/modules/agents/api/hooks';

interface Fiber {
	elementType: unknown;
	memoizedProps: { tile?: { key: string } } | null;
	child: Fiber | null;
	sibling: Fiber | null;
}

/** The committed props object of every `ApiRow`, by tile key. */
function rowProps(container: HTMLElement): Map<string, object> {
	const key = Object.keys(container).find((k) => k.startsWith('__reactContainer$'));
	const hostRoot = (container as unknown as Record<string, { stateNode: { current: Fiber } }>)[
		key as string
	];
	const out = new Map<string, object>();
	const stack: Fiber[] = [hostRoot.stateNode.current];
	while (stack.length > 0) {
		const fiber = stack.pop() as Fiber;
		if (fiber.elementType === ApiRow && fiber.memoizedProps?.tile) {
			out.set(fiber.memoizedProps.tile.key, fiber.memoizedProps);
		}
		if (fiber.sibling) stack.push(fiber.sibling);
		if (fiber.child) stack.push(fiber.child);
	}
	return out;
}

function apiRow(vendor: string, displayName: string): ApiResponse {
	return {
		_links: { self: `/apis/${vendor}`, openapi: `/apis/${vendor}/openapi` },
		api: { vendor, name: 'default', version: '1.0.0' },
		catalog_api_id: null,
		created_at: '2026-01-01T00:00:00Z',
		current_revision_id: null,
		description: null,
		display_name: displayName,
		icon_url: null,
		operation_count: 10,
		revision_count: 1,
		security_schemes: [],
		updated_at: '2026-01-01T00:00:00Z',
	} as unknown as ApiResponse;
}

describe('"Can call" row re-renders', () => {
	beforeEach(async () => {
		await page.viewport(1280, 900);
		setToken('test-token');
		window.localStorage.clear();
		resetAgentsStore();
		resetCredentialsStore([
			makeMockCredential({
				credential_id: 'cred_slack_1',
				name: 'Slack bot token',
				type: CredentialType.BEARER_TOKEN,
				api: { vendor: 'slack.com', name: 'default', version: '1.0.0' },
			}),
			makeMockCredential({
				credential_id: 'cred_github_1',
				name: 'GitHub PAT',
				type: CredentialType.BEARER_TOKEN,
				api: { vendor: 'github.com', name: 'default', version: '1.0.0' },
			}),
		]);
		resetApisStore([
			{ row: apiRow('slack.com', 'Slack'), spec: {} },
			{ row: apiRow('github', 'GitHub'), spec: {} },
		]);
		resetOrphanPurgeAttemptsForTest();
	});

	it('pinning one row re-renders that row only', async () => {
		const user = userEvent.setup();
		const { container } = renderWithProviders(<AgentsPage />, {
			route: '/?agent=agnt_active_1',
		});
		const rows = await screen.findAllByTestId('api-tile');
		expect(rows).toHaveLength(2);
		// The reads behind the rows (rules, usage, recent calls) settle first:
		// the props hold still across two looks a beat apart.
		let before = rowProps(container);
		await waitFor(async () => {
			await new Promise((r) => setTimeout(r, 150));
			const now = rowProps(container);
			const settled = [...now].every(([k, p]) => before.get(k) === p);
			before = now;
			expect(settled).toBe(true);
		});
		expect(before.size).toBe(2);

		await user.click(screen.getAllByTestId('row-header')[0]);
		await waitFor(() => expect(rows[0]).toHaveAttribute('data-pinned', 'true'));

		const after = rowProps(container);
		const rerendered = [...after].filter(([k, p]) => before.get(k) !== p);
		// The pinned row, and only it.
		expect(rerendered).toHaveLength(1);
		expect(rerendered[0][1]).toMatchObject({ pinned: true });
	});
});
