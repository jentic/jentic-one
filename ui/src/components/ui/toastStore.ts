/**
 * Tiny toast system (P8) — built in-house to avoid pulling in `sonner`
 * just for the credential close-the-loop signal. Safe-subset of what
 * a full toast library does:
 *
 *   - Multiple stacked toasts (newest on top).
 *   - Auto-dismiss after `durationMs` (default 5s).
 *   - Manual dismiss via the close button or programmatic id.
 *   - Optional action button.
 *   - One module-level store, so any consumer can call `toast(...)`
 *     without prop-drilling. The `<Toaster />` mount lives at the
 *     root layout and subscribes once.
 */

import { useEffect, useState } from 'react';

export type ToastVariant = 'default' | 'success' | 'error';

export interface ToastInput {
	id?: string;
	title: string;
	description?: string;
	variant?: ToastVariant;
	durationMs?: number;
	action?: {
		label: string;
		onClick: () => void;
	};
}

export interface ToastEntry extends Required<Omit<ToastInput, 'description' | 'action'>> {
	description?: string;
	action?: ToastInput['action'];
	createdAt: number;
}

type Listener = (toasts: ToastEntry[]) => void;

const DEFAULT_DURATION_MS = 5000;
let counter = 0;
let toasts: ToastEntry[] = [];
const listeners = new Set<Listener>();

function notify(): void {
	for (const fn of listeners) fn(toasts);
}

export function toast(input: ToastInput): string {
	const id = input.id ?? `t-${Date.now()}-${counter++}`;
	const entry: ToastEntry = {
		id,
		title: input.title,
		description: input.description,
		variant: input.variant ?? 'default',
		durationMs: input.durationMs ?? DEFAULT_DURATION_MS,
		action: input.action,
		createdAt: Date.now(),
	};
	// Dedup by id — re-emitting an id replaces the existing entry.
	toasts = [entry, ...toasts.filter((t) => t.id !== id)].slice(0, 5);
	notify();
	return id;
}

export function dismissToast(id: string): void {
	toasts = toasts.filter((t) => t.id !== id);
	notify();
}

export function clearAllToasts(): void {
	toasts = [];
	notify();
}

export function useToasts(): ToastEntry[] {
	const [snap, setSnap] = useState<ToastEntry[]>(toasts);
	useEffect(() => {
		const fn = (next: ToastEntry[]) => setSnap(next);
		listeners.add(fn);
		return () => {
			listeners.delete(fn);
		};
	}, []);
	return snap;
}
