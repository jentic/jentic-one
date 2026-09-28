import { describe, it, expect } from 'vitest';
import {
	auditChanges,
	auditChangeSummary,
	auditSentence,
	auditTargetLabel,
	auditTone,
} from '@/modules/monitor/lib/describeAudit';
import { formatSpan, jobSentence, originLabel } from '@/modules/monitor/lib/logVocabulary';

describe('audit sentences', () => {
	it('reads `noun.verb` actions as past-tense sentences', () => {
		expect(auditSentence({ action: 'job.cancel', target_type: 'job' })).toBe('Cancelled a job');
		expect(auditSentence({ action: 'execution.start', target_type: 'execution_record' })).toBe(
			'Started an execution',
		);
		expect(auditSentence({ action: 'oauth_grant.revoke', target_type: 'oauth_grant' })).toBe(
			'Revoked an OAuth grant',
		);
	});

	it('uses the whole phrase for verbs that already say everything', () => {
		expect(auditSentence({ action: 'user.login', target_type: 'session' })).toBe('Signed in');
		expect(auditSentence({ action: 'user.login_failed', target_type: 'session' })).toBe(
			'Failed to sign in',
		);
	});

	it('degrades unknown actions to readable words', () => {
		expect(auditSentence({ action: 'widget.re_index', target_type: 'widget' })).toBe(
			'Re index a widget',
		);
	});

	it('flags destructive and failed actions', () => {
		expect(auditTone({ action: 'credential.delete' })).toBe('warn');
		expect(auditTone({ action: 'user.login_failed' })).toBe('fail');
		expect(auditTone({ action: 'agent.approve' })).toBe('neutral');
	});

	it('labels target types without their article', () => {
		expect(auditTargetLabel('execution_record')).toBe('Execution');
		expect(auditTargetLabel('oauth_client')).toBe('OAuth client');
	});
});

describe('audit changes', () => {
	it('compares before/after key by key, skipping unchanged fields', () => {
		const changes = auditChanges({
			before: { status: 'running', kind: 'import' },
			after: { status: 'cancelled', kind: 'import' },
			diff: null,
		});
		expect(changes).toEqual([{ field: 'status', before: 'running', after: 'cancelled' }]);
		expect(auditChangeSummary(changes)).toBe('status running → cancelled');
	});

	it('prefers the recorded diff in either shape', () => {
		const changes = auditChanges({
			before: null,
			after: null,
			diff: { apis: [3, 4], scopes: { before: null, after: 'read' } },
		});
		expect(changes).toEqual([
			{ field: 'apis', before: '3', after: '4' },
			{ field: 'scopes', before: '—', after: 'read' },
		]);
		expect(auditChangeSummary(changes)).toBe('apis 3 → 4 · +1 more');
	});
});

describe('log vocabulary', () => {
	it('reads jobs as kind + state', () => {
		expect(jobSentence('import', 'running')).toBe('Import running');
		expect(jobSentence('spec_refresh', 'dead_letter')).toBe(
			'Spec refresh gave up after retries',
		);
	});

	it('formats spans compactly', () => {
		const at = (s: number) => new Date(Date.UTC(2026, 0, 1) + s * 1000).toISOString();
		expect(formatSpan(at(0), at(0.85))).toBe('850ms');
		expect(formatSpan(at(0), at(252))).toBe('4m 12s');
		expect(formatSpan(at(0), at(7200))).toBe('2h');
		expect(formatSpan(at(10), at(0))).toBeNull();
		expect(formatSpan(null, at(0))).toBeNull();
	});

	it('names origins the way the Origin picker does', () => {
		expect(originLabel('cli')).toBe('CLI');
		expect(originLabel('mcp')).toBe('MCP');
		expect(originLabel('webhook')).toBe('Webhook');
	});
});
