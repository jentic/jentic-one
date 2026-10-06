/**
 * AgentProvenance — where an agent came from and who vouched for it:
 * registration, approval, owner and parent agent. Rendered by the dock's
 * Settings sheet. A row is omitted when its fact is absent — an unapproved
 * agent has no approver.
 */
import { ActorLabel } from '@/shared/ui';
import { formatTimestamp } from '@/shared/lib/utils';
import { MetaItem } from '@/modules/agents/components/detail/shared';
import type { AgentEntity } from '@/modules/agents/api';

export function AgentProvenance({ agent }: { agent: AgentEntity }) {
	return (
		<dl data-testid="agent-provenance" className="grid grid-cols-2 gap-x-4 gap-y-3">
			<MetaItem label="Registered" value={formatTimestamp(agent.createdAt)} />
			{agent.attribution.registeredBy && (
				<MetaItem
					label="Registered by"
					value={<ActorLabel actorId={agent.attribution.registeredBy} />}
				/>
			)}
			{agent.approvedAt && (
				<MetaItem label="Approved" value={formatTimestamp(agent.approvedAt)} />
			)}
			{agent.attribution.approvedBy && (
				<MetaItem
					label="Approved by"
					value={<ActorLabel actorId={agent.attribution.approvedBy} />}
				/>
			)}
			{agent.ownerId && (
				<MetaItem label="Owner" value={<ActorLabel actorId={agent.ownerId} />} />
			)}
			{agent.parentAgentId && (
				<MetaItem
					label="Parent agent"
					value={<ActorLabel actorId={agent.parentAgentId} />}
				/>
			)}
		</dl>
	);
}
