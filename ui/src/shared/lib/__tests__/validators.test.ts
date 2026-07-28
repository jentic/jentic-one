import { describe, expect, it } from 'vitest';
import {
	validateName,
	validateDescription,
	NAME_MAX_LENGTH,
	DESCRIPTION_MAX_LENGTH,
} from '../validators';

/**
 * Locks the shared Edit-dialog validators (#15) so the agent + toolkit rename
 * dialogs keep identical caps and error copy after the extraction.
 */
describe('validateName', () => {
	it('rejects an empty / whitespace-only name', () => {
		expect(validateName('')).toBe("Name can't be empty.");
		expect(validateName('   ')).toBe("Name can't be empty.");
	});

	it('accepts a normal name', () => {
		expect(validateName('GitHub Tools')).toBeNull();
	});

	it('accepts a name exactly at the cap and rejects one over it', () => {
		expect(validateName('a'.repeat(NAME_MAX_LENGTH))).toBeNull();
		expect(validateName('a'.repeat(NAME_MAX_LENGTH + 1))).toBe(
			'Name must be 255 characters or fewer.',
		);
	});
});

describe('validateDescription', () => {
	it('accepts an empty description (optional field)', () => {
		expect(validateDescription('')).toBeNull();
	});

	it('accepts a description exactly at the cap and rejects one over it', () => {
		expect(validateDescription('a'.repeat(DESCRIPTION_MAX_LENGTH))).toBeNull();
		expect(validateDescription('a'.repeat(DESCRIPTION_MAX_LENGTH + 1))).toBe(
			'Description must be 1024 characters or fewer.',
		);
	});
});
