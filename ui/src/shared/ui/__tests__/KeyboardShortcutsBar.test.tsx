import { describe, expect, it } from 'vitest';
import { renderWithProviders, screen } from '@/__tests__/test-utils';
import { KeyboardShortcutsBar } from '@/shared/ui/KeyboardShortcutsBar';
import { isTypingTarget, MOD_KEY } from '@/shared/lib/keyboard';

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

	it('exposes a platform-aware modifier label', () => {
		expect(['⌘', 'Ctrl']).toContain(MOD_KEY);
	});
});

describe('KeyboardShortcutsBar', () => {
	it('renders nothing when there are no shortcuts', () => {
		const { container } = renderWithProviders(<KeyboardShortcutsBar shortcuts={[]} />);
		expect(container.querySelector('[data-testid="keyboard-shortcuts-bar"]')).toBeNull();
	});

	it('renders each shortcut label and its keys', () => {
		renderWithProviders(
			<KeyboardShortcutsBar
				placement="inline"
				shortcuts={[
					{ keys: ['↑', '↓'], label: 'Navigate' },
					{ keys: [MOD_KEY, '/'], label: 'Open help', chord: true },
				]}
			/>,
		);
		expect(screen.getByText('Navigate')).toBeInTheDocument();
		expect(screen.getByText('Open help')).toBeInTheDocument();
		expect(screen.getByTestId('keyboard-shortcuts-bar')).toBeVisible();
	});
});
