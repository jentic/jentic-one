/**
 * The operator's layout for a selected agent's "Can call" area: the
 * hover-reveal row tree (`list`, the default) or the dense identity grid (`cards`).
 *
 * A workspace-wide preference, not per agent: switching agents keeps the lens.
 * It persists through the shared `usePersistedChoice` — the same local
 * preference storage and `<module>.<camelCaseName>` key convention as the
 * Library workspace resizer (`library.workspaceWidth`).
 */
export type ApiView = 'list' | 'cards';

export const API_VIEWS: readonly ApiView[] = ['list', 'cards'];

export const DEFAULT_API_VIEW: ApiView = 'list';

/** The one localStorage key the lens persists under. */
export const API_VIEW_STORAGE_KEY = 'agents.apiView';
