import { afterEach, describe, expect, it } from 'vitest';
import { act, renderHook } from '@/__tests__/test-utils';
import { useNotificationsSeen } from '@/shared/attention/useNotificationsSeen';

const KEY = 'jentic-one.notifications.seen.anonymous';

describe('useNotificationsSeen', () => {
	afterEach(() => window.localStorage.clear());

	it('marks the given keys read and persists only those', () => {
		const { result } = renderHook(({ keys }) => useNotificationsSeen(keys), {
			initialProps: { keys: ['event:a', 'agent:b'] },
		});
		expect(result.current.unreadCount).toBe(2);
		act(() => result.current.markAllRead());
		expect(result.current.unreadCount).toBe(0);
		expect(result.current.isUnread('event:a')).toBe(false);
		expect(JSON.parse(window.localStorage.getItem(KEY) ?? '[]')).toEqual([
			'event:a',
			'agent:b',
		]);
	});

	it('counts a key that arrives later as unread', () => {
		window.localStorage.setItem(KEY, JSON.stringify(['event:a']));
		const { result } = renderHook(({ keys }) => useNotificationsSeen(keys), {
			initialProps: { keys: ['event:a', 'event:new'] },
		});
		expect(result.current.unreadCount).toBe(1);
		expect(result.current.isUnread('event:new')).toBe(true);
	});

	it('treats a malformed stored value as nothing read', () => {
		window.localStorage.setItem(KEY, '{not json');
		const { result } = renderHook(() => useNotificationsSeen(['event:a']));
		expect(result.current.unreadCount).toBe(1);
	});
});
