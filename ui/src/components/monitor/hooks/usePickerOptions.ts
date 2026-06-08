import { useEffect, useState } from 'react';
import { ToolkitsService } from '@/api/generated/services/ToolkitsService';
import { AgentsService } from '@/api/generated/services/AgentsService';

export interface PickerOption {
	value: string;
	label: string;
}

interface PickerState<T> {
	options: T[];
	isLoading: boolean;
	error: string | null;
}

/**
 * Fetches the tenant's toolkits and shapes them for the dropdown filter.
 * Always available — toolkits are visible to every authenticated session.
 */
export function useToolkitOptions(): PickerState<PickerOption> {
	const [state, setState] = useState<PickerState<PickerOption>>({
		options: [],
		isLoading: true,
		error: null,
	});

	useEffect(() => {
		let active = true;
		setState((s) => ({ ...s, isLoading: true, error: null }));

		ToolkitsService.listToolkitsToolkitsGet()
			.then((toolkits) => {
				if (!active) return;
				const list: Array<Record<string, unknown>> = Array.isArray(toolkits)
					? (toolkits as Array<Record<string, unknown>>)
					: [];
				const options = list
					.map((t) => {
						const id = (t.toolkit_id ?? t.id) as string | undefined;
						const name = (t.name as string | undefined) ?? id ?? '';
						return id ? { value: id, label: name || id } : null;
					})
					.filter((x): x is PickerOption => x !== null)
					.sort((a, b) => a.label.localeCompare(b.label));
				setState({ options, isLoading: false, error: null });
			})
			.catch((err: unknown) => {
				if (!active) return;
				setState({
					options: [],
					isLoading: false,
					error: err instanceof Error ? err.message : 'Failed to load toolkits',
				});
			});

		return () => {
			active = false;
		};
	}, []);

	return state;
}

/**
 * Fetches the tenant's agents — only meaningful for human (admin) sessions.
 * For agent JWT sessions, the API returns 401/403; we treat that as "no
 * options" rather than surfacing an error.
 */
export function useAgentOptions(enabled: boolean): PickerState<PickerOption> {
	const [state, setState] = useState<PickerState<PickerOption>>({
		options: [],
		isLoading: enabled,
		error: null,
	});

	useEffect(() => {
		if (!enabled) {
			setState({ options: [], isLoading: false, error: null });
			return;
		}
		let active = true;
		setState({ options: [], isLoading: true, error: null });

		AgentsService.listAgentsAgentsGet({})
			.then((response) => {
				if (!active) return;
				// /agents returns `{ agents: [...] }` — accept either shape so we
				// don't break if the contract ever flips back to a bare array.
				const list: Array<Record<string, unknown>> = Array.isArray(response)
					? (response as Array<Record<string, unknown>>)
					: Array.isArray((response as { agents?: unknown })?.agents)
						? (response as { agents: Array<Record<string, unknown>> }).agents
						: [];
				const options = list
					.map((a) => {
						const id = (a.client_id ?? a.agent_id ?? a.id) as string | undefined;
						const name =
							(a.client_name as string | undefined) ??
							(a.name as string | undefined) ??
							id ??
							'';
						return id ? { value: id, label: name || id } : null;
					})
					.filter((x): x is PickerOption => x !== null)
					.sort((x, y) => x.label.localeCompare(y.label));
				setState({ options, isLoading: false, error: null });
			})
			.catch(() => {
				if (!active) return;
				setState({ options: [], isLoading: false, error: null });
			});

		return () => {
			active = false;
		};
	}, [enabled]);

	return state;
}
