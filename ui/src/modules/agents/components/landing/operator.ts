/**
 * What the landing's operator panel shows: the two real surfaces a person sees
 * while agents work — the Notifications bell ("needs you") and the Activity
 * log (one list, filtered by source: calls, jobs, audit).
 */
import type { StatusGlyphTone } from '@/shared/ui';
import type { DemoApiId } from '@/modules/agents/components/landing/data/demoFixtures';

export type ActivitySource = 'calls' | 'jobs' | 'audit';

/** The agent's status row: `null` = not registered yet, else a canonical actor status. */
export type OperatorAgentStatus = null | 'pending' | 'active' | 'disabled';

/** Where the scripted pointer in the operator panel rests. */
export type OperatorPointer = 'none' | 'approve' | 'pause';

export interface ActivityRow {
	id: string;
	source: ActivitySource;
	tone: StatusGlyphTone;
	/** Screen-reader status word ("Completed", "Denied"). */
	statusLabel: string;
	/** The sentence — an operation id for calls (rendered mono). */
	title: string;
	api?: DemoApiId;
	/** Right-hand detail: an HTTP status, a scope. */
	detail?: string;
	/** The call's short tag ("1".."4"), shown on the lane's call chip too. */
	tag?: string;
}

export interface BellItem {
	id: string;
	title: string;
	detail: string;
	/** The verb that clears it ("Approve", "Approve and connect"). */
	action: string;
}

/** The Activity log's source filter, in the Monitor's own words. */
export const ACTIVITY_SOURCE_OPTIONS = [
	{ value: 'all', label: 'All' },
	{ value: 'calls', label: 'Calls' },
	{ value: 'jobs', label: 'Jobs' },
	{ value: 'audit', label: 'Audit' },
] as const;

export type ActivityFilter = (typeof ACTIVITY_SOURCE_OPTIONS)[number]['value'];
