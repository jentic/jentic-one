import { renderWithProviders, screen, checkA11y } from '@/__tests__/test-utils';
import { OperationDetail } from '@/shared/ui/OperationDetail';
import type { OperationDetailData, SecuritySchemeMap } from '@/shared/ui/OperationDetail';

function makeOp(overrides: Partial<OperationDetailData> = {}): OperationDetailData {
	return {
		method: 'GET',
		path: '/v1/accounts',
		summary: 'List accounts',
		description: undefined,
		parameters: [],
		security: [],
		...overrides,
	};
}

describe('OperationDetail', () => {
	it('renders the method + path heading by default', () => {
		renderWithProviders(<OperationDetail operation={makeOp()} />);
		expect(screen.getByText('GET')).toBeInTheDocument();
		expect(screen.getByText('/v1/accounts')).toBeInTheDocument();
	});

	it('hides the method + path heading when showHeader is false', () => {
		renderWithProviders(<OperationDetail operation={makeOp()} showHeader={false} />);
		expect(screen.queryByText('GET')).not.toBeInTheDocument();
		// The caller (enclosing row) already shows method/path.
		expect(screen.queryByText('/v1/accounts')).not.toBeInTheDocument();
	});

	it('suppresses a summary that merely echoes the path while the header is shown', () => {
		renderWithProviders(<OperationDetail operation={makeOp({ summary: '/v1/accounts' })} />);
		// The path appears once (in the header), not duplicated as a summary line.
		expect(screen.getAllByText('/v1/accounts')).toHaveLength(1);
	});

	it('still shows a path-echoing summary when the header is hidden', () => {
		renderWithProviders(
			<OperationDetail operation={makeOp({ summary: '/v1/accounts' })} showHeader={false} />,
		);
		expect(screen.getByText('/v1/accounts')).toBeInTheDocument();
	});

	it('renders a description distinct from the summary as markdown', () => {
		renderWithProviders(
			<OperationDetail
				operation={makeOp({
					summary: 'List accounts',
					description: 'Returns **all** accounts',
				})}
			/>,
		);
		expect(screen.getByText('List accounts')).toBeInTheDocument();
		expect(screen.getByText('all')).toBeInTheDocument();
	});

	it('does not repeat the description when it is identical to the summary', () => {
		renderWithProviders(
			<OperationDetail
				operation={makeOp({ summary: 'Same text', description: 'Same text' })}
			/>,
		);
		expect(screen.getAllByText('Same text')).toHaveLength(1);
	});

	it('renders a parameters table with name/in/required cells', () => {
		renderWithProviders(
			<OperationDetail
				operation={makeOp({
					parameters: [
						{ name: 'limit', in: 'query', required: true, description: 'Max rows' },
						{ name: 'cursor', in: 'query', required: false },
					],
				})}
			/>,
		);
		expect(screen.getByText('Parameters')).toBeInTheDocument();
		expect(screen.getByText('limit')).toBeInTheDocument();
		expect(screen.getByText('cursor')).toBeInTheDocument();
		expect(screen.getByText('yes')).toBeInTheDocument();
		expect(screen.getByText('Max rows')).toBeInTheDocument();
	});

	it('caps the parameters table at 20 rows and shows an overflow note', () => {
		const parameters = Array.from({ length: 25 }, (_, i) => ({
			name: `p${i}`,
			in: 'query',
			required: false,
		}));
		renderWithProviders(<OperationDetail operation={makeOp({ parameters })} />);
		expect(screen.getByText('p0')).toBeInTheDocument();
		expect(screen.getByText('p19')).toBeInTheDocument();
		expect(screen.queryByText('p20')).not.toBeInTheDocument();
		expect(screen.getByText('+ 5 more parameters')).toBeInTheDocument();
	});

	it('omits the Parameters section when there are none', () => {
		renderWithProviders(<OperationDetail operation={makeOp()} />);
		expect(screen.queryByText('Parameters')).not.toBeInTheDocument();
	});

	it('resolves the Authentication table from the security_schemes map', () => {
		const schemes: SecuritySchemeMap = {
			bearerAuth: {
				type: 'http',
				scheme: 'bearer',
				description: 'JWT bearer token',
			},
		};
		renderWithProviders(
			<OperationDetail
				operation={makeOp({ security: ['bearerAuth'] })}
				securitySchemes={schemes}
			/>,
		);
		expect(screen.getByText('Authentication')).toBeInTheDocument();
		expect(screen.getByText('bearerAuth')).toBeInTheDocument();
		expect(screen.getByText('(http, bearer)')).toBeInTheDocument();
		expect(screen.getByText('JWT bearer token')).toBeInTheDocument();
	});

	it('includes the "in <loc>" hint for apiKey-style schemes', () => {
		const schemes: SecuritySchemeMap = {
			apiKey: { type: 'apiKey', in: 'header' },
		};
		renderWithProviders(
			<OperationDetail
				operation={makeOp({ security: ['apiKey'] })}
				securitySchemes={schemes}
			/>,
		);
		expect(screen.getByText('(apiKey, in header)')).toBeInTheDocument();
	});

	it('lists a referenced scheme even when it is absent from the schemes map', () => {
		renderWithProviders(<OperationDetail operation={makeOp({ security: ['unknown'] })} />);
		expect(screen.getByText('Authentication')).toBeInTheDocument();
		expect(screen.getByText('unknown')).toBeInTheDocument();
	});

	it('omits the Authentication section when the operation requires none', () => {
		renderWithProviders(<OperationDetail operation={makeOp()} />);
		expect(screen.queryByText('Authentication')).not.toBeInTheDocument();
	});

	it('has no a11y violations with parameters and auth populated', async () => {
		const { container } = renderWithProviders(
			<OperationDetail
				operation={makeOp({
					description: 'Returns a list of accounts.',
					parameters: [
						{ name: 'limit', in: 'query', required: true, description: 'Max rows' },
					],
					security: ['bearerAuth'],
				})}
				securitySchemes={{ bearerAuth: { type: 'http', scheme: 'bearer' } }}
			/>,
		);
		await checkA11y(container);
	});
});
