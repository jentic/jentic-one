import { renderWithProviders, screen, userEvent, checkA11y } from '@/__tests__/test-utils';
import { DataTable, type Column } from '@/shared/ui/DataTable';

interface Row {
	id: string;
	name: string;
	count: number;
}

const columns: Column<Row>[] = [
	{ key: 'name', header: 'Name' },
	{ key: 'count', header: 'Count' },
];

const data: Row[] = [
	{ id: '1', name: 'Alpha', count: 3 },
	{ id: '2', name: 'Beta', count: 7 },
];

describe('DataTable', () => {
	it('renders headers and rows', () => {
		renderWithProviders(<DataTable columns={columns} data={data} getRowKey={(r) => r.id} />);
		expect(screen.getByText('Name')).toBeInTheDocument();
		expect(screen.getByText('Alpha')).toBeInTheDocument();
		expect(screen.getByText('7')).toBeInTheDocument();
	});

	it('shows the empty message when there is no data', () => {
		renderWithProviders(
			<DataTable
				columns={columns}
				data={[]}
				getRowKey={(r) => r.id}
				emptyMessage="Nothing yet"
			/>,
		);
		expect(screen.getByText('Nothing yet')).toBeInTheDocument();
	});

	it('shows a loading state', () => {
		renderWithProviders(
			<DataTable columns={columns} data={[]} getRowKey={(r) => r.id} isLoading />,
		);
		expect(screen.getByRole('status')).toBeInTheDocument();
	});

	it('fires onRowClick', async () => {
		const user = userEvent.setup();
		const onRowClick = vi.fn();
		renderWithProviders(
			<DataTable
				columns={columns}
				data={data}
				getRowKey={(r) => r.id}
				onRowClick={onRowClick}
			/>,
		);
		await user.click(screen.getByText('Alpha'));
		expect(onRowClick).toHaveBeenCalledWith(data[0]);
	});

	it('wraps the table in a keyboard-focusable, labelled scroll region', () => {
		renderWithProviders(
			<DataTable
				columns={columns}
				data={data}
				getRowKey={(r) => r.id}
				ariaLabel="Recent activity"
			/>,
		);
		const region = screen.getByRole('region', { name: 'Recent activity' });
		expect(region).toHaveAttribute('tabindex', '0');
		expect(region).toContainElement(screen.getByRole('table'));
	});

	it('falls back to a generic region label', () => {
		renderWithProviders(<DataTable columns={columns} data={data} getRowKey={(r) => r.id} />);
		expect(screen.getByRole('region', { name: 'Scrollable table' })).toBeInTheDocument();
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderWithProviders(
			<DataTable columns={columns} data={data} getRowKey={(r) => r.id} />,
		);
		await checkA11y(container);
	});

	describe('responsive card layout', () => {
		// Force the mobile branch by stubbing matchMedia → matches. Restored after.
		function mockMobile(matches: boolean) {
			const original = window.matchMedia;
			window.matchMedia = ((query: string) => ({
				matches,
				media: query,
				onchange: null,
				addEventListener: () => {},
				removeEventListener: () => {},
				addListener: () => {},
				removeListener: () => {},
				dispatchEvent: () => false,
			})) as unknown as typeof window.matchMedia;
			return () => {
				window.matchMedia = original;
			};
		}

		it('renders cards instead of a table on small screens', () => {
			const restore = mockMobile(true);
			try {
				renderWithProviders(
					<DataTable
						columns={columns}
						data={data}
						getRowKey={(r) => r.id}
						ariaLabel="Things"
						renderCard={(r) => <span>card:{r.name}</span>}
					/>,
				);
				// The mobile branch is a labelled list, not a table.
				expect(screen.queryByRole('table')).not.toBeInTheDocument();
				expect(screen.getByRole('list', { name: 'Things' })).toBeInTheDocument();
				expect(screen.getByText('card:Alpha')).toBeInTheDocument();
				// No duplicate rendering: the desktop cell text is absent.
				expect(screen.queryByText('Alpha')).not.toBeInTheDocument();
			} finally {
				restore();
			}
		});

		it('fires onRowClick from a card and exposes the row label', async () => {
			const restore = mockMobile(true);
			try {
				const user = userEvent.setup();
				const onRowClick = vi.fn();
				renderWithProviders(
					<DataTable
						columns={columns}
						data={data}
						getRowKey={(r) => r.id}
						onRowClick={onRowClick}
						getRowLabel={(r) => `Open ${r.name}`}
						renderCard={(r) => <span>card:{r.name}</span>}
					/>,
				);
				await user.click(screen.getByRole('button', { name: 'Open Alpha' }));
				expect(onRowClick).toHaveBeenCalledWith(data[0]);
			} finally {
				restore();
			}
		});

		it('keeps the table when no card renderer is supplied (mobile fallback)', () => {
			const restore = mockMobile(true);
			try {
				renderWithProviders(
					<DataTable columns={columns} data={data} getRowKey={(r) => r.id} />,
				);
				// Without renderCard, the table still renders (it just scrolls).
				expect(screen.getByRole('table')).toBeInTheDocument();
			} finally {
				restore();
			}
		});
	});
});
