import { describe, expect, it } from 'vitest';
import { isTypingTarget } from '@/shared/lib/keyboard';

describe('isTypingTarget', () => {
	it('returns true for form fields and contentEditable, false otherwise', () => {
		const input = document.createElement('input');
		const textarea = document.createElement('textarea');
		const select = document.createElement('select');
		const div = document.createElement('div');
		const editable = document.createElement('div');
		editable.contentEditable = 'true';

		expect(isTypingTarget(input)).toBe(true);
		expect(isTypingTarget(textarea)).toBe(true);
		expect(isTypingTarget(select)).toBe(true);
		expect(isTypingTarget(editable)).toBe(true);
		expect(isTypingTarget(div)).toBe(false);
		expect(isTypingTarget(null)).toBe(false);
	});
});
