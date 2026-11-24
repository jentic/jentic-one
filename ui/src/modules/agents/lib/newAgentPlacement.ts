/**
 * Where `New agent` lives on the Agents page: `'rail-end'` (the agent strip's end;
 * the header drops its button while the strip shows) or `'header'` (the page
 * header). Set `NEW_AGENT_PLACEMENT` to `'header'` to switch.
 */
export type NewAgentPlacement = 'rail-end' | 'header';

export const NEW_AGENT_PLACEMENT: NewAgentPlacement = 'rail-end';
