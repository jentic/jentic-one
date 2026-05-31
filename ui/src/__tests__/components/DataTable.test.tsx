import { render, screen, fireEvent } from '@testing-library/react';
import axe from 'axe-core';
import { DataTable, Column } from '@/components/ui/DataTable';

interface User {
	id: string;
	name: string;
	email: string;
}

const columns: Column<User>[] = [
	{ key: 'name', header: 'Name' },
	{ key: 'email', header: 'Email' },
];

const data: User[] = [
	{ id: '1', name: 'Alice', email: 'alice@example.com' },
	{ id: '2', name: 'Bob', email: 'bob@example.com' },
];

describe('DataTable', () => {
	it('renders headers and data rows', () => {
		render(<DataTable columns={columns} data={data} getRowKey={(r) => r.id} />);
		expect(screen.getByRole('columnheader', { name: 'Name' })).toBeInTheDocument();
		expect(screen.getByRole('cell', { name: 'Alice' })).toBeInTheDocument();
		expect(screen.getByRole('cell', { name: 'bob@example.com' })).toBeInTheDocument();
	});

	it('shows empty message when data is empty', () => {
		render(
			<DataTable columns={columns} data={[]} getRowKey={(r) => r.id} emptyMessage="Empty." />,
		);
		expect(screen.getByText('Empty.')).toBeInTheDocument();
		expect(screen.queryByRole('table')).not.toBeInTheDocument();
	});

	it('shows loading state', () => {
		render(<DataTable columns={columns} data={[]} getRowKey={(r) => r.id} isLoading />);
		expect(screen.getByRole('status')).toBeInTheDocument();
	});

	it('calls onRowClick when row is clicked', () => {
		const onRowClick = vi.fn();
		render(
			<DataTable
				columns={columns}
				data={data}
				getRowKey={(r) => r.id}
				onRowClick={onRowClick}
			/>,
		);
		fireEvent.click(screen.getByRole('cell', { name: 'Alice' }));
		expect(onRowClick).toHaveBeenCalledWith(data[0]);
	});

	it('uses custom render function for columns', () => {
		const cols: Column<User>[] = [
			{
				key: 'name',
				header: 'Name',
				render: (row) => <strong>{row.name.toUpperCase()}</strong>,
			},
			{ key: 'email', header: 'Email' },
		];
		render(<DataTable columns={cols} data={data} getRowKey={(r) => r.id} />);
		expect(screen.getByText('ALICE')).toBeInTheDocument();
	});

	it('renders clickable rows with cursor-pointer class', () => {
		const onRowClick = vi.fn();
		render(
			<DataTable
				columns={columns}
				data={data}
				getRowKey={(r) => r.id}
				onRowClick={onRowClick}
			/>,
		);
		const row = screen.getByRole('cell', { name: 'Alice' }).closest('tr')!;
		expect(row.className).toContain('cursor-pointer');
	});

	it('has no accessibility violations', async () => {
		const { container } = render(
			<DataTable columns={columns} data={data} getRowKey={(r) => r.id} />,
		);
		const results = await axe.run(container);
		expect(results.violations).toEqual([]);
	});
});
