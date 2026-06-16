import { useCallback, useReducer } from 'react';
import type { ApiOut } from '@/api/types';

/**
 * State machine for the multi-step "Add credential" dialog.
 *
 * Three orthogonal pieces of state collapse into a single hook so
 * every host (toolkit page, credentials page, API detail, Discover
 * sheet) drives the dialog through the same surface:
 *
 *  - `step` — which sub-screen is showing.
 *  - `mode` — what Save means at the end.
 *      'workspace' → POST /credentials, done.
 *      'toolkit'   → POST /credentials → POST /toolkits/{id}/credentials.
 *  - `selectedApi` — the API the credential is about; pre-known when
 *    a host opens us with `openForApi(apiOut)`, otherwise filled by
 *    the SearchApi step.
 *
 * The reducer is intentionally side-effect free; mutations live in
 * the dialog component (where toast/error/redirect concerns belong).
 *
 * Why a hook + reducer rather than React Context: the dialog is
 * always rendered inside a single host page, so a hook colocated with
 * that host gives us straightforward per-page state without leaking
 * dialog state across routes. The hook returns a stable `state` and
 * a small set of action creators that hosts call to open the dialog
 * in the appropriate mode.
 */

export type AddCredentialMode = 'workspace' | 'toolkit';

export type AddCredentialStep = 'search' | 'existing' | 'configure' | 'confirm';

export interface AddCredentialState {
	open: boolean;
	step: AddCredentialStep;
	mode: AddCredentialMode;
	/** Set iff `mode === 'toolkit'`. Drives the post-save bind call. */
	toolkitId: string | null;
	/** Optional human label for the toolkit, surfaced in the confirm step. */
	toolkitName: string | null;
	/** Set when a host opens the dialog already knowing the API
	 *  (Discover sheet, API detail page). Skips the search step. */
	selectedApi: ApiOut | null;
	/** Saved credential id after Configure → success. Used by the
	 *  toolkit-mode bind step and by the Confirm step's summary. */
	savedCredentialId: string | null;
}

const INITIAL_STATE: AddCredentialState = {
	open: false,
	step: 'search',
	mode: 'workspace',
	toolkitId: null,
	toolkitName: null,
	selectedApi: null,
	savedCredentialId: null,
};

type Action =
	| {
			type: 'open';
			mode: AddCredentialMode;
			toolkitId?: string | null;
			toolkitName?: string | null;
			selectedApi?: ApiOut | null;
			step?: AddCredentialStep;
	  }
	| { type: 'close' }
	| { type: 'goToStep'; step: AddCredentialStep }
	| { type: 'setSelectedApi'; api: ApiOut | null }
	| { type: 'setSavedCredentialId'; id: string | null };

function reducer(state: AddCredentialState, action: Action): AddCredentialState {
	switch (action.type) {
		case 'open': {
			const selectedApi = action.selectedApi ?? null;
			return {
				...INITIAL_STATE,
				open: true,
				mode: action.mode,
				toolkitId: action.toolkitId ?? null,
				toolkitName: action.toolkitName ?? null,
				selectedApi,
				step: action.step ?? (selectedApi ? 'existing' : 'search'),
			};
		}
		case 'close':
			return { ...INITIAL_STATE };
		case 'goToStep':
			return { ...state, step: action.step };
		case 'setSelectedApi':
			return {
				...state,
				selectedApi: action.api,
				step: action.api ? 'existing' : 'search',
			};
		case 'setSavedCredentialId':
			return { ...state, savedCredentialId: action.id };
		default:
			return state;
	}
}

export interface UseAddCredentialDialog {
	state: AddCredentialState;
	openWorkspace: () => void;
	openForToolkit: (toolkitId: string, toolkitName?: string | null) => void;
	openForApi: (api: ApiOut, opts?: { mode?: AddCredentialMode; toolkitId?: string }) => void;
	close: () => void;
	goToStep: (step: AddCredentialStep) => void;
	setSelectedApi: (api: ApiOut | null) => void;
	setSavedCredentialId: (id: string | null) => void;
}

export function useAddCredentialDialog(): UseAddCredentialDialog {
	const [state, dispatch] = useReducer(reducer, INITIAL_STATE);

	const openWorkspace = useCallback(() => {
		dispatch({ type: 'open', mode: 'workspace' });
	}, []);

	const openForToolkit = useCallback((toolkitId: string, toolkitName?: string | null) => {
		dispatch({ type: 'open', mode: 'toolkit', toolkitId, toolkitName });
	}, []);

	const openForApi = useCallback(
		(api: ApiOut, opts?: { mode?: AddCredentialMode; toolkitId?: string }) => {
			dispatch({
				type: 'open',
				mode: opts?.mode ?? 'workspace',
				toolkitId: opts?.toolkitId ?? null,
				selectedApi: api,
			});
		},
		[],
	);

	const close = useCallback(() => dispatch({ type: 'close' }), []);
	const goToStep = useCallback(
		(step: AddCredentialStep) => dispatch({ type: 'goToStep', step }),
		[],
	);
	const setSelectedApi = useCallback(
		(api: ApiOut | null) => dispatch({ type: 'setSelectedApi', api }),
		[],
	);
	const setSavedCredentialId = useCallback(
		(id: string | null) => dispatch({ type: 'setSavedCredentialId', id }),
		[],
	);

	return {
		state,
		openWorkspace,
		openForToolkit,
		openForApi,
		close,
		goToStep,
		setSelectedApi,
		setSavedCredentialId,
	};
}
