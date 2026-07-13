import { useEffect, useState } from 'react';
import { getHealth, type Health } from '@/shared/api';

type HealthState =
	{ status: 'loading' } | { status: 'error'; error: Error } | { status: 'ready'; data: Health };

export function useHealth(): HealthState {
	const [state, setState] = useState<HealthState>({ status: 'loading' });

	useEffect(() => {
		let active = true;
		getHealth()
			.then((data) => {
				if (active) setState({ status: 'ready', data });
			})
			.catch((error: unknown) => {
				if (active) {
					setState({
						status: 'error',
						error: error instanceof Error ? error : new Error('Unknown error'),
					});
				}
			});
		return () => {
			active = false;
		};
	}, []);

	return state;
}
