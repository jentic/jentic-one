/**
 * Public compatibility facade for the Activity stream layer.
 *
 * Pure event types, wire adaptation, toast-scope storage, display formatting,
 * navigation, and trace-bundle helpers live in `agentStreamModel.ts`; the React
 * context, SSE lifecycle, backlog pagination, and query-cache invalidation
 * provider live in `AgentStreamProvider.tsx`.
 */

export {
	ACTIVITY_CATEGORIES,
	adaptEvent,
	buildGroupKeyForTest,
	buildTraceBundle,
	categoryForKind,
	conflictHint,
	EVENTS_FORBIDDEN_COPY,
	formatFailurePillCount,
	formatStreamAgo,
	formatStreamDateTimeParts,
	formatStreamDayLabel,
	formatStreamTime,
	freezeFeed,
	inlineActionsFor,
	isAfterFreeze,
	isApprovalEvent,
	isApprovalRequestEvent,
	isFailureSeverity,
	isRetiredEventType,
	kindForType,
	matchesActivityScope,
	matchesToastScope,
	primaryDestinationFor,
	RAIL_AUDIO_STORAGE_KEY,
	RAIL_COLLAPSED_STORAGE_KEY,
	railGroupTitle,
	railTitle,
	readToastScope,
	recentFailureCount,
	severityForWire,
	STREAM_KIND_LABEL,
	streamDayKey,
	TOAST_SCOPE_CHANGE_EVENT,
	TOAST_SCOPE_STORAGE_KEY,
	writeToastScope,
} from '@/shared/lib/agentStreamModel';
export type {
	ActivityCategory,
	ActivityScope,
	ConflictDigests,
	FeedFreeze,
	InlineActionKind,
	InlineActionSpec,
	StreamEvent,
	StreamKind,
	StreamLinks,
	StreamSeverity,
	StreamStatus,
	StreamTokens,
	ToastScope,
	TraceBundle,
} from '@/shared/lib/agentStreamModel';
export {
	AgentStreamProvider,
	useAgentStream,
	useAgentStreamOptional,
} from '@/shared/lib/AgentStreamProvider';
