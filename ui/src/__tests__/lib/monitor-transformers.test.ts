import { describe, expect, it } from 'vitest';
import {
	mapExecutionStatus,
	vendorFromOperation,
	apiDisplayFromTrace,
	traceToLogEntry,
	jobToLogEntry,
	jobToJobLogEntry,
	mergeInFlightWithHistory,
	usageToMonitorStats,
	usageToTopRows,
	statusFilterToBackend,
} from '@/lib/monitor-transformers';
import type { JobOut, TraceOut, UsageResponse } from '@/api/types';

describe('monitor-transformers', () => {
	it('maps backend status strings onto the UI ExecutionStatus enum', () => {
		expect(mapExecutionStatus('success')).toBe('COMPLETED');
		expect(mapExecutionStatus('completed')).toBe('COMPLETED');
		expect(mapExecutionStatus('failed')).toBe('FAILED');
		expect(mapExecutionStatus('running')).toBe('RUNNING');
		expect(mapExecutionStatus('pending')).toBe('QUEUED');
		expect(mapExecutionStatus(null)).toBe('QUEUED');
		expect(mapExecutionStatus('weird')).toBe('QUEUED');
	});

	it('extracts a vendor slug from operation_id (legacy and broker formats)', () => {
		// Legacy dotted format
		expect(vendorFromOperation('slack.chat.post')).toBe('slack');
		expect(vendorFromOperation(null)).toBe('unknown');
		expect(vendorFromOperation(undefined)).toBe('unknown');

		// Production format: METHOD/host/path (broker calls)
		expect(vendorFromOperation('GET/api.stripe.com/v1/payment_intents')).toBe('api.stripe.com');
		expect(vendorFromOperation('POST/api.slack.com/api/chat.postMessage')).toBe(
			'api.slack.com',
		);
		// Host-only edge case (no path segment)
		expect(vendorFromOperation('GET/api.example.com')).toBe('api.example.com');
	});

	it('apiDisplayFromTrace prefers backend-joined api_name, then api_id, then derived vendor', () => {
		// 1. Backend joined name wins.
		expect(
			apiDisplayFromTrace({
				id: 't1',
				api_name: 'Stripe API',
				api_id: 'stripe.com',
				operation_id: 'GET/api.stripe.com/v1/charges',
				status: 'success',
			} as unknown as TraceOut),
		).toBe('Stripe API');

		// 2. Falls back to api_id when name missing.
		expect(
			apiDisplayFromTrace({
				id: 't2',
				api_name: null,
				api_id: 'unregistered.example.com',
				operation_id: 'GET/unregistered.example.com/x',
				status: 'success',
			} as unknown as TraceOut),
		).toBe('unregistered.example.com');

		// 3. Legacy: no api_id at all → derive from operation_id.
		expect(
			apiDisplayFromTrace({
				id: 't3',
				operation_id: 'github.repos.list',
				status: 'success',
			} as unknown as TraceOut),
		).toBe('github');
	});

	it('converts a TraceOut into an ExecutionLogEntry', () => {
		const trace: TraceOut = {
			id: 'tr-1',
			toolkit_id: 'tk-1',
			operation_id: 'GET/api.github.com/repos',
			workflow_id: null,
			status: 'success',
			duration_ms: 124,
			created_at: 1_700_000_000,
			completed_at: 1_700_000_001,
			http_status: 200,
			error: null,
			steps: [],
			agent_id: 'agent-7',
			api_id: 'github.com',
			api_name: 'GitHub',
		};
		const entry = traceToLogEntry(
			trace,
			new Map([['tk-1', 'GitHub Toolkit']]),
			new Map([['agent-7', 'Bot 7']]),
		);
		expect(entry.executionId).toBe('tr-1');
		expect(entry.executionType).toBe('operation');
		expect(entry.status).toBe('COMPLETED');
		expect(entry.toolkitName).toBe('GitHub Toolkit');
		expect(entry.agentName).toBe('Bot 7');
		// Vendor slug is the catalog id, NOT the upstream host — keeps palette
		// stable across api.github.com / github.com.
		expect(entry.apiVendor).toBe('github.com');
		// Display name is the catalog `apis.name`.
		expect(entry.apiName).toBe('GitHub');
		expect(entry.durationMs).toBe(124);
	});

	it('falls back to api_id then operation_id when apis.name is missing', () => {
		const traceWithIdOnly = {
			id: 'tr-id-only',
			operation_id: 'GET/unregistered.example.com/x',
			status: 'success',
			created_at: 1_700_000_000,
			api_id: 'unregistered.example.com',
			api_name: null,
		} as unknown as TraceOut;
		expect(traceToLogEntry(traceWithIdOnly).apiName).toBe('unregistered.example.com');

		const legacyTrace = {
			id: 'tr-legacy',
			operation_id: 'github.repos.list',
			status: 'success',
			created_at: 1_700_000_000,
		} as unknown as TraceOut;
		expect(traceToLogEntry(legacyTrace).apiName).toBe('github');
	});

	it('converts in-flight jobs into ExecutionLogEntry rows', () => {
		const job: JobOut = {
			job_id: 'job-1',
			tenant_id: 'tn-1',
			capability: 'slack.chat.post',
			toolkit_id: 'tk-2',
			agent_id: null,
			status: 'running',
			created_at: 1_700_000_000,
			completed_at: null,
			trace_id: null,
		} as JobOut;
		const entry = jobToLogEntry(job, new Map([['tk-2', 'Slack']]));
		expect(entry.executionId).toBe('job-1');
		expect(entry.executionType).toBe('job');
		expect(entry.isJobOnly).toBe(true);
		expect(entry.toolkitName).toBe('Slack');
		expect(entry.apiVendor).toBe('slack');
	});

	it('dedupes traces with corresponding completed jobs in mergeInFlightWithHistory', () => {
		const trace = traceToLogEntry({
			id: 'shared-id',
			operation_id: 'x.y',
			status: 'success',
			created_at: 1_700_000_001,
		} as TraceOut);
		const job = jobToLogEntry({
			job_id: 'shared-id',
			capability: 'x.y',
			status: 'pending',
			created_at: 1_700_000_000,
			toolkit_id: null,
			agent_id: null,
		} as unknown as JobOut);

		const merged = mergeInFlightWithHistory([trace], [job]);
		expect(merged).toHaveLength(1);
		expect(merged[0].executionType).toBe('job');
	});

	it('builds MonitorStats from a UsageResponse', () => {
		const usage: UsageResponse = {
			since: 0,
			until: 100,
			bucket_seconds: 60,
			group_by: 'toolkit',
			top_limit: 10,
			stats: {
				total: 100,
				success: 90,
				failed: 10,
				pending: 0,
				avg_ms: 250,
				p50_ms: 200,
				p95_ms: 600,
				active_now: 4,
			},
			buckets: [],
			top: [],
		};
		const stats = usageToMonitorStats(usage);
		expect(stats.totalExecutions).toBe(100);
		expect(stats.successRate).toBeCloseTo(90);
		expect(stats.failureCount).toBe(10);
		expect(stats.activeNow).toBe(4);
		expect(stats.avgLatencyMs).toBe(250);
	});

	it('extracts API and toolkit summaries from usage top rows', () => {
		const apiUsage: UsageResponse = {
			since: 0,
			until: 100,
			bucket_seconds: 60,
			group_by: 'api',
			top_limit: 10,
			stats: {
				total: 0,
				success: 0,
				failed: 0,
				pending: 0,
				avg_ms: 0,
				p50_ms: null,
				p95_ms: null,
				active_now: 0,
			},
			buckets: [],
			top: [
				{
					key: 'github',
					label: 'github.com/repos',
					total: 50,
					success: 45,
					failed: 5,
					avg_ms: 200,
					trend: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 0, 0],
				},
			],
		};
		const toolkitUsage: UsageResponse = {
			...apiUsage,
			group_by: 'toolkit',
			top: [
				{
					key: 'tk-1',
					label: 'My Toolkit',
					total: 20,
					success: 20,
					failed: 0,
					avg_ms: 110,
					trend: [0, 0, 5, 5, 5, 5, 0, 0, 0, 0, 0, 0],
				},
			],
		};
		const { apis, toolkits } = usageToTopRows(apiUsage, toolkitUsage);
		expect(apis).toHaveLength(1);
		expect(apis[0]).toMatchObject({
			vendor: 'github',
			apiName: 'github.com/repos',
			totalExecutions: 50,
			avgLatencyMs: 200,
		});
		expect(apis[0].successRate).toBe(90);
		expect(apis[0].recentTrend).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 0, 0]);

		expect(toolkits).toHaveLength(1);
		expect(toolkits[0]).toMatchObject({
			toolkitId: 'tk-1',
			toolkitName: 'My Toolkit',
			totalExecutions: 20,
			successRate: 100,
			avgLatencyMs: 110,
		});
		expect(toolkits[0].recentTrend).toEqual([0, 0, 5, 5, 5, 5, 0, 0, 0, 0, 0, 0]);
	});

	it('maps the UI status filter back to the backend representation', () => {
		expect(statusFilterToBackend('ALL')).toBeNull();
		expect(statusFilterToBackend(null)).toBeNull();
		expect(statusFilterToBackend('COMPLETED')).toBe('success');
		expect(statusFilterToBackend('FAILED')).toBe('failed');
		expect(statusFilterToBackend('RUNNING')).toBe('running');
		expect(statusFilterToBackend('QUEUED')).toBe('pending');
	});

	it('carries job_id and parent_trace_id from TraceOut into ExecutionLogEntry', () => {
		const trace: TraceOut = {
			id: 'tr-xlink',
			operation_id: 'github.repos.list',
			status: 'success',
			created_at: 1_700_000_000,
			job_id: 'job-7',
			parent_trace_id: 'tr-parent',
		} as TraceOut;
		const entry = traceToLogEntry(trace);
		expect(entry.jobId).toBe('job-7');
		expect(entry.parentTraceId).toBe('tr-parent');
	});

	it('preserves job_id on jobToLogEntry rows and leaves parentTraceId null', () => {
		const job: JobOut = {
			job_id: 'job-only',
			capability: 'slack.chat.post',
			status: 'pending',
			created_at: 1_700_000_000,
			toolkit_id: null,
			agent_id: null,
		} as unknown as JobOut;
		const entry = jobToLogEntry(job);
		expect(entry.jobId).toBe('job-only');
		expect(entry.parentTraceId).toBeNull();
	});

	describe('jobToJobLogEntry', () => {
		const baseJob = {
			job_id: 'job-cancel',
			capability: 'slack.chat.post',
			status: 'failed',
			error: 'Cancelled by client',
			created_at: 1_700_000_000,
			completed_at: 1_700_000_010,
			toolkit_id: 'tk-2',
			agent_id: 'agent-1',
			trace_id: 'tr-1',
			http_status: null,
			upstream_job_url: null,
			kind: 'broker',
		} as unknown as JobOut;

		it('synthesises a cancelled status from failed + "Cancelled by client" marker', () => {
			const entry = jobToJobLogEntry(baseJob);
			expect(entry.status).toBe('cancelled');
			// Cancellation marker is consumed, not echoed back as an error.
			expect(entry.errorMessage).toBeNull();
		});

		it('keeps a real failed-status when error message is anything else', () => {
			const job = { ...baseJob, error: 'upstream 500' } as unknown as JobOut;
			const entry = jobToJobLogEntry(job);
			expect(entry.status).toBe('failed');
			expect(entry.errorMessage).toBe('upstream 500');
		});

		it('reads kind=workflow as workflow, unknown values fall back to "unknown"', () => {
			const wfJob = { ...baseJob, kind: 'workflow' } as unknown as JobOut;
			const oddJob = { ...baseJob, kind: 'something-else' } as unknown as JobOut;
			expect(jobToJobLogEntry(wfJob).kind).toBe('workflow');
			expect(jobToJobLogEntry(oddJob).kind).toBe('unknown');
		});

		it('computes durationMs from created_at/completed_at when both present', () => {
			const entry = jobToJobLogEntry(baseJob);
			// 1_700_000_010 - 1_700_000_000 = 10s = 10_000ms
			expect(entry.durationMs).toBe(10_000);
		});

		it('leaves durationMs undefined when completed_at is missing', () => {
			const job = { ...baseJob, completed_at: null } as unknown as JobOut;
			const entry = jobToJobLogEntry(job);
			expect(entry.durationMs).toBeUndefined();
		});

		it('resolves toolkit and agent display names from the supplied maps', () => {
			const entry = jobToJobLogEntry(
				baseJob,
				new Map([['tk-2', 'Slack Toolkit']]),
				new Map([['agent-1', 'Bot 1']]),
			);
			expect(entry.toolkitName).toBe('Slack Toolkit');
			expect(entry.agentName).toBe('Bot 1');
		});
	});
});
