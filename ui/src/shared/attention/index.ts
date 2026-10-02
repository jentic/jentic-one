export { useAttentionItems, attentionKeys } from '@/shared/attention/useAttentionItems';
export type {
	AttentionItem,
	AttentionKind,
	AttentionState,
	AttentionUrgency,
} from '@/shared/attention/useAttentionItems';
export { AttentionList } from '@/shared/attention/AttentionList';
export type { AttentionListProps } from '@/shared/attention/AttentionList';
export { useAcknowledgeAttention, useApproveAgent } from '@/shared/attention/actions';
