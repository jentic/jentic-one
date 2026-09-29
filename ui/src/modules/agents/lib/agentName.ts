/**
 * The agent name rule, shared by every surface that names an agent (the create
 * sheet and the landing's register command).
 */

/** The backend's limit for an agent name (`AgentCreateRequest.name` and
 * `RegisterRequest.client_name` both cap at 255). */
export const AGENT_NAME_MAX_LENGTH = 255;

/** Why `name` can't name an agent, or `null` when it can. */
export function agentNameError(name: string): string | null {
	return name.trim() ? null : 'A name is required.';
}
