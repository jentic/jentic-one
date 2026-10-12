/**
 * Which notifications this browser has marked read, per signed-in user.
 *
 * Read state is the viewer's own and lives only in this browser: the server
 * keeps no acknowledgement (events are an append-only record). "Mark all as
 * read" remembers the keys of the items on screen; an item stays unread while
 * its key is not remembered, so anything that arrives later counts again. The
 * remembered keys are pruned to the items currently listed, so a resolved
 * item's key does not linger.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useOptionalCurrentUser } from '@/shared/auth';

const STORAGE_PREFIX = 'jentic-one.notifications.seen.';

function storageKey(userId: string): string {
	return `${STORAGE_PREFIX}${userId}`;
}

function readSeen(key: string): Set<string> {
	try {
		const raw = window.localStorage.getItem(key);
		const parsed: unknown = raw ? JSON.parse(raw) : [];
		return new Set(Array.isArray(parsed) ? parsed.filter((k) => typeof k === 'string') : []);
	} catch {
		// Storage blocked or a malformed value: nothing is marked read.
		return new Set();
	}
}

function writeSeen(key: string, keys: Iterable<string>): void {
	try {
		window.localStorage.setItem(key, JSON.stringify([...keys]));
	} catch {
		// Storage blocked: the marks last until the page reloads.
	}
}

export interface NotificationsSeen {
	/** Whether the item with this key is still unread. */
	isUnread: (itemKey: string) => boolean;
	/** How many of `itemKeys` are unread. */
	unreadCount: number;
	/** Mark every item in `itemKeys` read. */
	markAllRead: () => void;
}

export function useNotificationsSeen(itemKeys: readonly string[]): NotificationsSeen {
	const userId = useOptionalCurrentUser()?.id ?? 'anonymous';
	const key = storageKey(userId);
	const [seen, setSeen] = useState<Set<string>>(() => readSeen(key));

	// Another user signing in on this browser has their own marks.
	useEffect(() => setSeen(readSeen(key)), [key]);

	// Follow a "Mark all as read" made in another tab.
	useEffect(() => {
		const onStorage = (event: StorageEvent): void => {
			if (event.key === key) setSeen(readSeen(key));
		};
		window.addEventListener('storage', onStorage);
		return () => window.removeEventListener('storage', onStorage);
	}, [key]);

	const isUnread = useCallback((itemKey: string) => !seen.has(itemKey), [seen]);
	const unreadCount = useMemo(
		() => itemKeys.filter((itemKey) => !seen.has(itemKey)).length,
		[itemKeys, seen],
	);
	const markAllRead = useCallback(() => {
		const next = new Set(itemKeys);
		writeSeen(key, next);
		setSeen(next);
	}, [itemKeys, key]);

	return { isUnread, unreadCount, markAllRead };
}
