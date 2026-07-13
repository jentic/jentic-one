import { describe, expect, it, vi } from 'vitest';
import { render, screen, userEvent, within } from '@/__tests__/test-utils';
import { OperationsDialog } from '@/shared/app/rail/OperationsDialog';
import type { PermissionRule } from '@/shared/lib';

const noop = () => {};

/** A grant with a large allow list — the case the dialog exists to handle. */
function bigRules(count: number): PermissionRule[] {
	return [
		{
			effect: 'allow',
			methods: ['GET'],
			operations: Array.from({ length: count }, (_, i) => `op_${i}`),
		},
		{ effect: 'deny', methods: ['DELETE'], operations: ['op_delete_all'] },
	];
}

function open(rules: PermissionRule[], onClose = noop) {
	return render(<OperationsDialog open onClose={onClose} rules={rules} targetLabel="github" />);
}

describe('OperationsDialog', () => {
	it('renders every operation, no matter how many', () => {
		open(bigRules(100));
		const dialog = screen.getByRole('dialog');
		// All 100 allow ops plus the single deny op are present in the DOM.
		expect(within(dialog).getByText('op_0')).toBeInTheDocument();
		expect(within(dialog).getByText('op_99')).toBeInTheDocument();
		expect(within(dialog).getByText('op_delete_all')).toBeInTheDocument();
	});

	it('summarises magnitude and target in the subtitle', () => {
		open(bigRules(100));
		// 100 allow + 1 deny = 101 operations across 2 rules.
		expect(screen.getByText(/101 operations across 2 rules/)).toBeInTheDocument();
		expect(screen.getByText(/github/)).toBeInTheDocument();
	});

	it('filters operations live by query with a running count', async () => {
		const user = userEvent.setup();
		open(bigRules(20));
		const filter = screen.getByLabelText('Filter operations');
		await user.type(filter, 'op_1');
		// op_1, op_10..op_19 = 11 matches.
		expect(await screen.findByText(/11 operations match/)).toBeInTheDocument();
		const dialog = screen.getByRole('dialog');
		expect(within(dialog).getByText('op_19')).toBeInTheDocument();
		// A non-matching op is filtered out.
		expect(within(dialog).queryByText('op_0')).not.toBeInTheDocument();
	});

	it('reports when nothing matches the query', async () => {
		const user = userEvent.setup();
		open(bigRules(15));
		await user.type(screen.getByLabelText('Filter operations'), 'zzz-nope');
		expect(await screen.findByText(/No operations match/)).toBeInTheDocument();
	});

	it('explains each effect present in the grant, including "Needs approval"', () => {
		open([
			{ effect: 'allow', operations: ['a', 'b'] },
			{ effect: 'require-approval', methods: ['POST'], operations: ['transfer'] },
		]);
		const dialog = screen.getByRole('dialog');
		// The legend spells out what require-approval does at call time so the
		// reviewer doesn't have to guess — each held call files a new request.
		expect(
			within(dialog).getByText(/held and files a new access request/i),
		).toBeInTheDocument();
		// Allow is explained too.
		expect(within(dialog).getByText(/no human in the loop/i)).toBeInTheDocument();
	});

	it('hides the filter for small grants', () => {
		open([{ effect: 'allow', operations: ['a', 'b'] }]);
		expect(screen.queryByLabelText('Filter operations')).not.toBeInTheDocument();
	});

	it('closes via the Close button', async () => {
		const user = userEvent.setup();
		const onClose = vi.fn();
		open(bigRules(10), onClose);
		await user.click(screen.getByRole('button', { name: 'Close' }));
		expect(onClose).toHaveBeenCalled();
	});
});
