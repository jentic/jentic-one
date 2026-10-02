/**
 * AgentProvenance — where an agent came from and who vouched for it:
 * registration, approval, owner and parent agent. One component because the flat
 * surface's Settings sheet and the console's Overview tab render the same block.
 * A row is omitted when its fact is absent — an unapproved agent has no approver.
 */
import { ActorLabel } from '@/shared/ui';
import { formatTimestamp } from '@/shared/lib/utils';
import { MetaItem } from '@/modules/agents/components/detail/shared';
import type { AgentEntity } from '@/modules/agents/api';

interface AgentProvenanceProps {
	agent: AgentEntity;
	/** Grid density: the console has a full-width tab, a sheet has one column
	 * of roughly 420px. */
	columns?: 'sheet' | 'page';
}

export function AgentProvenance({ agent, columns = 'page' }: AgentProvenanceProps) {
	return (
		<dl
			data-testid="agent-provenance"
			className={
				columns === 'sheet'
					? 'grid grid-cols-2 gap-x-4 gap-y-3'
					: 'grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3 lg:grid-cols-4'
			}
		>
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
