/**
 * `useVendorOperations` over a paged list: `data` is only ever the WHOLE list
 * (its readers count against it); `useVendorOperationsProgress` offers pages read so far.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { renderWithProviders, screen, waitFor } from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { setToken } from '@/shared/api';
import {
	useVendorOperations,
	useVendorOperationsProgress,
} from '@/shared/credentials/api/vendors-hooks';
import { useAllowedOperationCount } from '@/shared/credentials/components/OperationImpactPreview';

const API = { vendor: 'acme.com', name: 'main', version: '1.0.0' };
const OP_A = { operation_id: 'listItems', method: 'GET', path: '/items', name: 'List items' };
const OP_B = { operation_id: 'createItem', method: 'POST', path: '/items', name: 'Create item' };

/** Two pages; the second held until `open()`. Counts first-page reads. */
function twoPages() {
	let open!: () => void;
	const wait = new Promise<void>((r) => (open = r));
	const reads = { first: 0 };
	worker.use(
		http.get('/apis/acme.com/main/1.0.0/operations', async ({ request }) => {
			const cursor = new URL(request.url).searchParams.get('cursor');
			if (!cursor) {
				reads.first += 1;
				return HttpResponse.json({ data: [OP_A], has_more: true, next_cursor: 'p2' });
			}
			await wait;
			return HttpResponse.json({ data: [OP_B], has_more: false, next_cursor: null });
		}),
	);
	return { open, reads };
}

function Probe({ refetchRef }: { refetchRef?: { current: (() => void) | null } }) {
	const whole = useVendorOperations(API);
	const load = useVendorOperationsProgress(API);
	const counted = useAllowedOperationCount(API, [{ effect: 'allow', path: '.*' }]);
	if (refetchRef) refetchRef.current = () => void whole.refetch();
	return (
		<dl>
			<dd data-testid="whole">
				{whole.data === undefined
					? 'undefined'
					: whole.data === null
						? 'null'
						: whole.data.data.map((o) => o.operation_id).join(',')}
			</dd>
			<dd data-testid="loaded">{load.operations.map((o) => o.operation_id).join(',')}</dd>
			<dd data-testid="complete">{String(load.complete)}</dd>
			<dd data-testid="counted">
				{counted === undefined
					? 'undefined'
					: counted === null
						? 'null'
						: `${counted.allowed}/${counted.total}`}
			</dd>
		</dl>
	);
}

describe('useVendorOperations over a paged list', () => {
	beforeEach(() => setToken('test-token'));

	it('keeps `data` the whole list while the progress hook offers the first page', async () => {
		const pages = twoPages();
		renderWithProviders(<Probe />);
		await waitFor(() => expect(screen.getByTestId('loaded')).toHaveTextContent('listItems'));
		expect(screen.getByTestId('whole')).toHaveTextContent('undefined');
		expect(screen.getByTestId('complete')).toHaveTextContent('false');
		expect(screen.getByTestId('counted')).toHaveTextContent('undefined');

		pages.open();
		await waitFor(() =>
			expect(screen.getByTestId('whole')).toHaveTextContent('listItems,createItem'),
		);
		expect(screen.getByTestId('loaded')).toHaveTextContent('listItems,createItem');
		expect(screen.getByTestId('complete')).toHaveTextContent('true');
		expect(screen.getByTestId('counted')).toHaveTextContent('2/2');
	});

	it('a refetch over a list already held never shrinks it back to one page', async () => {
		const pages = twoPages();
		pages.open();
		const refetchRef: { current: (() => void) | null } = { current: null };
		renderWithProviders(<Probe refetchRef={refetchRef} />);
		await waitFor(() => expect(screen.getByTestId('complete')).toHaveTextContent('true'));

		// Watch every render of the progress list across the refetch.
		const seen = new Set<string>();
		const observer = new MutationObserver(() =>
			seen.add(screen.getByTestId('loaded').textContent ?? ''),
		);
		observer.observe(screen.getByTestId('loaded'), {
			childList: true,
			characterData: true,
			subtree: true,
		});
		refetchRef.current?.();
		await waitFor(() => expect(pages.reads.first).toBe(2));
		await new Promise((r) => setTimeout(r, 100));
		observer.disconnect();
		expect([...seen].filter((v) => v !== 'listItems,createItem')).toEqual([]);
		expect(screen.getByTestId('whole')).toHaveTextContent('listItems,createItem');
	});

	it('an API not imported yet (404) reads as null on both, with nothing partial left over', async () => {
		worker.use(
			http.get('/apis/acme.com/main/1.0.0/operations', () =>
				HttpResponse.json({ status: 404 }, { status: 404 }),
			),
		);
		renderWithProviders(<Probe />);
		await waitFor(() => expect(screen.getByTestId('whole')).toHaveTextContent('null'));
		expect(screen.getByTestId('loaded')).toBeEmptyDOMElement();
		expect(screen.getByTestId('complete')).toHaveTextContent('false');
		expect(screen.getByTestId('counted')).toHaveTextContent('null');
	});

	it('a read that fails after its first page leaves no partial list behind', async () => {
		worker.use(
			http.get('/apis/acme.com/main/1.0.0/operations', ({ request }) => {
				const cursor = new URL(request.url).searchParams.get('cursor');
				if (!cursor) {
					return HttpResponse.json({ data: [OP_A], has_more: true, next_cursor: 'p2' });
				}
				return HttpResponse.json({ detail: 'boom' }, { status: 500 });
			}),
		);
		renderWithProviders(<Probe />);
		await waitFor(() => expect(screen.getByTestId('counted')).toHaveTextContent('null'));
		expect(screen.getByTestId('loaded')).toBeEmptyDOMElement();
		expect(screen.getByTestId('whole')).toHaveTextContent('undefined');
	});
});
