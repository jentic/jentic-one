import { renderHook, act } from '@testing-library/react';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom';
import { useCredentialEditSheet } from '@/hooks/useCredentialEditSheet';

/**
 * Wrap the hook in a MemoryRouter so `useSearchParams` has a router
 * context. We expose the current location to the test by rendering it
 * into the wrapper's children — that way assertions can look at the
 * URL after each `openSheet` / `closeSheet`.
 */
function makeWrapper(initialUrl: string) {
	let lastSearch = '';
	const Capture = () => {
		const loc = useLocation();
		lastSearch = loc.search;
		return null;
	};
	const wrapper = ({ children }: { children: React.ReactNode }) => (
		<MemoryRouter initialEntries={[initialUrl]}>
			<Routes>
				<Route
					path="*"
					element={
						<>
							{children}
							<Capture />
						</>
					}
				/>
			</Routes>
		</MemoryRouter>
	);
	return { wrapper, getSearch: () => lastSearch };
}

describe('useCredentialEditSheet', () => {
	it('reflects the URL ?edit param on mount', () => {
		const { wrapper } = makeWrapper('/credentials?edit=cred-1');
		const { result } = renderHook(() => useCredentialEditSheet(), { wrapper });

		expect(result.current.editId).toBe('cred-1');
		expect(result.current.stickyId).toBe('cred-1');
		expect(result.current.open).toBe(true);
	});

	it('starts closed when no ?edit is present', () => {
		const { wrapper } = makeWrapper('/credentials');
		const { result } = renderHook(() => useCredentialEditSheet(), { wrapper });

		expect(result.current.editId).toBeNull();
		expect(result.current.stickyId).toBeNull();
		expect(result.current.open).toBe(false);
	});

	it('openSheet sets the search param and stickyId', () => {
		const { wrapper, getSearch } = makeWrapper('/credentials');
		const { result } = renderHook(() => useCredentialEditSheet(), { wrapper });

		act(() => result.current.openSheet('cred-2'));

		expect(getSearch()).toBe('?edit=cred-2');
		expect(result.current.editId).toBe('cred-2');
		expect(result.current.stickyId).toBe('cred-2');
		expect(result.current.open).toBe(true);
	});

	it('closeSheet drops the URL param but keeps stickyId until cleared', () => {
		const { wrapper, getSearch } = makeWrapper('/credentials?edit=cred-3');
		const { result } = renderHook(() => useCredentialEditSheet(), { wrapper });

		act(() => result.current.closeSheet());

		expect(getSearch()).toBe('');
		expect(result.current.editId).toBeNull();
		expect(result.current.open).toBe(false);
		// Sticky still holds the prior id so a sheet body that
		// renders the credential doesn't blank during the close
		// animation.
		expect(result.current.stickyId).toBe('cred-3');

		act(() => result.current.clearSticky());
		expect(result.current.stickyId).toBeNull();
	});

	it('preserves other search params when toggling ?edit', () => {
		const { wrapper, getSearch } = makeWrapper('/credentials?tab=oauth');
		const { result } = renderHook(() => useCredentialEditSheet(), { wrapper });

		act(() => result.current.openSheet('cred-9'));

		const params = new URLSearchParams(getSearch());
		expect(params.get('edit')).toBe('cred-9');
		expect(params.get('tab')).toBe('oauth');

		act(() => result.current.closeSheet());

		const after = new URLSearchParams(getSearch());
		expect(after.get('edit')).toBeNull();
		expect(after.get('tab')).toBe('oauth');
	});

	it('honours a custom paramName', () => {
		const { wrapper, getSearch } = makeWrapper('/credentials?inspect=cred-4');
		const { result } = renderHook(() => useCredentialEditSheet({ paramName: 'inspect' }), {
			wrapper,
		});

		expect(result.current.editId).toBe('cred-4');

		act(() => result.current.closeSheet());
		expect(getSearch()).toBe('');
	});
});
