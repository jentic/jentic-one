import {
	deriveTileStatus,
	isBlockedStatus,
	TILE_STATUS_LABEL,
} from '@/modules/agents/lib/tileStatus';

const live = { suspended: false, agentServing: true, awaitingConsent: false };
const rules = (allow: number, deny: number) => ({ total: allow + deny, allow, deny });

describe('deriveTileStatus', () => {
	it('is Ready only with at least one allow rule on a live, serving binding', () => {
		expect(deriveTileStatus({ ...live, rules: rules(1, 0) })).toBe('ready');
		expect(deriveTileStatus({ ...live, rules: rules(2, 3) })).toBe('ready');
	});

	it('says Blocked when there are no rules, never Ready', () => {
		const status = deriveTileStatus({ ...live, rules: rules(0, 0) });
		expect(status).toBe('blocked-no-rules');
		expect(isBlockedStatus(status)).toBe(true);
		expect(TILE_STATUS_LABEL[status]).toBe('Blocked · no rules');
	});

	it('says Blocked · all denied when every rule denies', () => {
		expect(deriveTileStatus({ ...live, rules: rules(0, 2) })).toBe('blocked-all-denied');
	});

	it('never claims Ready over rules it has not read', () => {
		expect(deriveTileStatus({ ...live, rules: undefined })).toBe('checking');
		expect(deriveTileStatus({ ...live, rules: 'loading' })).toBe('checking');
		expect(TILE_STATUS_LABEL.checking).toBe('Checking access…');
	});

	it('reads Status unavailable when the rules read failed — an unknown binding may be blocked', () => {
		const status = deriveTileStatus({ ...live, rules: 'error' });
		expect(status).toBe('unavailable');
		expect(TILE_STATUS_LABEL[status]).toBe('Status unavailable');
		expect(isBlockedStatus(status)).toBe(false);
	});

	it('lets the higher-precedence states win over an unread rules state', () => {
		expect(deriveTileStatus({ ...live, suspended: true, rules: 'error' })).toBe('suspended');
		expect(deriveTileStatus({ ...live, awaitingConsent: true, rules: 'loading' })).toBe(
			'sign-in-needed',
		);
	});

	it('follows the precedence suspended → not serving → sign-in → blocked → ready', () => {
		const none = rules(0, 0);
		expect(
			deriveTileStatus({
				suspended: true,
				agentServing: false,
				awaitingConsent: true,
				rules: none,
			}),
		).toBe('suspended');
		expect(
			deriveTileStatus({
				suspended: false,
				agentServing: false,
				awaitingConsent: true,
				rules: none,
			}),
		).toBe('not-serving');
		expect(
			deriveTileStatus({
				suspended: false,
				agentServing: true,
				awaitingConsent: true,
				rules: none,
			}),
		).toBe('sign-in-needed');
		expect(deriveTileStatus({ ...live, rules: none })).toBe('blocked-no-rules');
	});

	it('only the two Blocked statuses are blocked', () => {
		for (const s of ['ready', 'suspended', 'not-serving', 'sign-in-needed'] as const) {
			expect(isBlockedStatus(s)).toBe(false);
		}
	});
});
