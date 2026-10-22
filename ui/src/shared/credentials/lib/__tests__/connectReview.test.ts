import { describe, expect, it } from 'vitest';
import { describeConfirmError } from '@/shared/credentials/lib/connectReview';

describe('describeConfirmError', () => {
	it('ends the dialog when the agent has a suspended binding for the API', () => {
		const copy = describeConfirmError({
			status: 409,
			code: 'connect_session_binding_suspended',
			message: "agent 'agt_1' has a suspended credential binding for this API",
		});

		expect(copy.action).toBe('ended');
		expect(copy.message).toBe(
			"This agent's access to this API is suspended. Resume or remove that binding before approving a new connection.",
		);
		expect(copy.message).not.toContain('agt_1');
	});
});
