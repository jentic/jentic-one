/**
 * Words the agent strip, its hover card and the agent picker share, so a count
 * reads the same on every surface.
 */
import type { AgentEntity } from '@/modules/agents/api';

export function plural(n: number, word: string): string {
	return `${n} ${n === 1 ? word : `${word}s`}`;
}

/** The fleet's size as far as it is known: "38", or "38+" while later pages
 * are still to come (or one failed), so a partial count never reads as whole. */
export function fleetCount(n: number, incomplete: boolean): string {
	return incomplete ? `${n}+` : String(n);
}

/** "3 APIs · 9 credentials" — the tab badge's reading. */
export function badgeLabel(apiCount: number, credentialCount: number | undefined): string {
	const apis = plural(apiCount, 'API');
	return credentialCount == null ? apis : `${apis} · ${plural(credentialCount, 'credential')}`;
}

/** A short reading of an agent for labels: its wait, or what it can reach. */
export function agentGist(agent: AgentEntity, apiCount: number | undefined): string | null {
	if (agent.status === 'pending') return 'waiting for approval';
	if (agent.status === 'archived') return 'archived';
	if (agent.status === 'rejected') return 'rejected';
	if (agent.status === 'disabled') return 'disabled';
	return apiCount == null ? null : plural(apiCount, 'API');
}

/** Agents whose name or id holds the query (case-insensitive). */
export function matchesAgent(agent: AgentEntity, query: string): boolean {
	const q = query.trim().toLowerCase();
	if (!q) return true;
	return agent.name.toLowerCase().includes(q) || agent.id.toLowerCase().includes(q);
}
