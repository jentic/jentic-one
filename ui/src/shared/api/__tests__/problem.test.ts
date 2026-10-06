import { describe, it, expect } from 'vitest';
import { problemDetailText } from '@/shared/api';

describe('problemDetailText', () => {
	it('returns a string detail', () => {
		expect(problemDetailText({ title: 'Bad Request', detail: 'name is taken' })).toBe(
			'name is taken',
		);
	});

	it('ignores a blank detail and never falls back to the title', () => {
		expect(problemDetailText({ title: 'Bad Request', detail: '  ' })).toBeNull();
		expect(problemDetailText({ title: 'Bad Request' })).toBeNull();
	});

	it('ignores a non-string detail (FastAPI 422 field errors)', () => {
		expect(
			problemDetailText({ detail: [{ loc: ['body', 'name'], msg: 'required' }] }),
		).toBeNull();
	});

	it('returns null for a body that is not an object', () => {
		expect(problemDetailText(undefined)).toBeNull();
		expect(problemDetailText(null)).toBeNull();
		expect(problemDetailText('oops')).toBeNull();
	});
});
