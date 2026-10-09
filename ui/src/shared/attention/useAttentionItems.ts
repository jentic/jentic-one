/**
 * The ONE definition of "what needs a human right now".
 *
 * Before this hook, four surfaces each counted attention their own way (the
 * Notifications bell, the Agents nav badge, the rail's failure pill, Monitor's
 * "Flagged" filter) and the bell double-counted a self-registered agent
 * (once as a pending agent, once as its `agent.self_registered` alert). Every
 * surface that shows an attention count or list now reads this hook, so the
 * numbers agree by construction.
 *
 * Sources, each its own query so one failing endpoint degrades only its rows.
 * A source the caller has no permission for is skipped (no request, not
 * reported as failed); a source that is read and fails is always reported:
 *   - agents awaiting approval      (`GET /agents?status=pending`, drained, `agents:read`)
 *   - OAuth clients awaiting review (`GET /admin/oauth-clients?approval_status=pending`, org:admin)
 *   - recent action events          (`GET /events?requires_action=true&from=<24h ago>`,
 *                                    `events:read`)
 *   - credentials whose OAuth sign-in never finished (joined from the credential list,
 *                                    `credentials:read` or `owner:credentials:read`)
 *
 * Events that merely MIRROR a queue item (an agent's self-registration, a DCR
 * client's registration) are dropped — the queue row is the actionable one —
 * as are catalog updates, whose current state lives on the Workspace page.
 *
 * Events are append-only history, so nothing clears an alert's
 * `requires_action`; the alert source is bounded to the last
 * {@link ALERT_WINDOW_MS} instead, so alerts age out of the inbox rather than
 * accumulating forever.
 */
import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
	EventsService,
	OAuthClientsService,
	sharedQueryKeys,
	type AgentResponse,
	type EventResponse,
	type OAuthClientResponse,
} from '@/shared/api';
import { usePendingAgentsCount } from '@/shared/hooks/usePendingAgentsCount';
import { useAllCredentials } from '@/shared/credentials/api';
import { credentialAwaitsConsent } from '@/shared/credentials/lib/credentialIdentity';
import { adaptEvent, primaryDestinationFor, severityForWire } from '@/shared/lib/agentStream';
import { useOptionalCurrentUser } from '@/shared/auth/AuthContext';
import { ORG_ADMIN } from '@/shared/auth/usePermission';
import { useCanReadEvents } from '@/shared/auth/useCanReadEvents';
import { CREDENTIALS_READ, OWNER_CREDENTIALS_READ, useCanAccess } from '@/shared/auth/useCanAccess';

export type AttentionKind = 'agent' | 'oauth_client' | 'credential' | 'event';

/** Higher sorts first. Failures outrank approvals; approvals outrank setup nags. */
export type AttentionUrgency = 3 | 2 | 1;

export interface AttentionItem {
	/** Stable across refetches: `<kind>:<id>`. */
	key: string;
	kind: AttentionKind;
	urgency: AttentionUrgency;
	title: string;
	detail: string | null;
	/** ISO timestamp the item started waiting. */
	since: string;
	/** Router-relative deep link to where the item is resolved. */
	href: string | null;
	/** Source record, for surfaces that offer inline actions. */
	agent?: AgentResponse;
	event?: EventResponse;
	oauthClient?: OAuthClientResponse;
}

export interface AttentionState {
	items: AttentionItem[];
	count: number;
	/** True while any source is still on its first load. */
	isLoading: boolean;
	/** Sources that failed, by human label — rendered as a degraded note. */
	failedSources: string[];
}

export const attentionKeys = {
	events: [...sharedQueryKeys.attentionRoot, 'events'] as const,
	oauthClients: [...sharedQueryKeys.attentionRoot, 'oauth-clients'] as const,
};

const REFETCH_MS = 45_000;

/** How far back the alert source reads `requires_action` events. */
export const ALERT_WINDOW_MS = 24 * 60 * 60 * 1000;

/**
 * Event types the inbox skips. Registrations duplicate a live queue row (the
 * queue row wins). A catalog update's outstanding-ness lives on the API's
 * Workspace page, not in the event — listing it here would keep an adopted
 * update counted until it aged out.
 */
const SKIPPED_EVENT_TYPES = new Set([
	'agent.self_registered',
	'oauth_client.registered',
	'catalog.update_available',
	'catalog.update_conflicts_overlay',
]);

export function useAttentionItems(): AttentionState {
	// Optional-auth read (not `usePermission`) so shell chrome that mounts this
	// hook — the Activity rail — also renders in tests without an AuthProvider.
	const isAdmin = useOptionalCurrentUser()?.permissions?.includes(ORG_ADMIN) ?? false;
	const pendingAgents = usePendingAgentsCount();
	// A caller who cannot read events has no event alerts to miss, so the source
	// is off rather than reported as failed.
	const canReadEvents = useCanReadEvents();

	const events = useQuery({
		queryKey: attentionKeys.events,
		queryFn: () =>
			EventsService.listEvents({
				requiresAction: true,
				from: new Date(Date.now() - ALERT_WINDOW_MS).toISOString(),
				limit: 50,
			}),
		enabled: canReadEvents,
		staleTime: 30_000,
		refetchInterval: REFETCH_MS,
	});

	const oauthClients = useQuery({
		queryKey: attentionKeys.oauthClients,
		queryFn: () => OAuthClientsService.listOauthClients({ approvalStatus: 'pending' }),
		enabled: isAdmin,
		staleTime: 30_000,
		refetchInterval: REFETCH_MS,
	});

	const canReadCredentials = useCanAccess(CREDENTIALS_READ, OWNER_CREDENTIALS_READ);
	const credentials = useAllCredentials({ enabled: canReadCredentials });

	const items = useMemo<AttentionItem[]>(() => {
		const out: AttentionItem[] = [];

		for (const agent of pendingAgents.agents) {
			out.push({
				key: `agent:${agent.id}`,
				kind: 'agent',
				urgency: 2,
				title: `${agent.name} is waiting for approval`,
				detail: agent.description ?? null,
				since: agent.created_at,
				href: `/agents?agent=${encodeURIComponent(agent.id)}`,
				agent,
			});
		}

		for (const client of oauthClients.data?.data ?? []) {
			out.push({
				key: `oauth_client:${client.id}`,
				kind: 'oauth_client',
				urgency: 2,
				title: `OAuth client ${client.name} wants to connect`,
				detail: client.description ?? null,
				since: client.created_at,
				href: '/settings?tab=queue',
				oauthClient: client,
			});
		}

		if (credentials.complete) {
			for (const credential of credentials.items) {
				if (!credentialAwaitsConsent(credential)) continue;
				out.push({
					key: `credential:${credential.credential_id}`,
					kind: 'credential',
					urgency: 1,
					title: `${credential.name} sign-in isn't finished`,
					detail: 'Agents cannot use this credential until someone completes the sign-in.',
					since: credential.created_at,
					href: '/agents?credentials=1',
				});
			}
		}

		for (const event of events.data?.data ?? []) {
			if (SKIPPED_EVENT_TYPES.has(event.type)) continue;
			const severity = severityForWire(event.severity);
			out.push({
				key: `event:${event.event_id}`,
				kind: 'event',
				urgency: severity === 'critical' || severity === 'error' ? 3 : 1,
				title: event.summary,
				detail: event.detail ?? null,
				since: event.created_at,
				href: primaryDestinationFor(adaptEvent(event)),
				event,
			});
		}

		return out.sort(
			(a, b) => b.urgency - a.urgency || Date.parse(a.since) - Date.parse(b.since),
		);
	}, [
		pendingAgents.agents,
		oauthClients.data,
		credentials.complete,
		credentials.items,
		events.data,
	]);

	const failedSources = [
		pendingAgents.isError && 'agent approvals',
		events.isError && 'alerts',
		oauthClients.isError && 'OAuth client queue',
		credentials.error != null && 'credentials',
	].filter((s): s is string => typeof s === 'string');

	return {
		items,
		count: items.length,
		isLoading: pendingAgents.isLoading || events.isLoading || oauthClients.isLoading,
		failedSources,
	};
}
