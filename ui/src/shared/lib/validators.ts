/**
 * Shared name/description validators for the agent + toolkit Edit dialogs.
 *
 * Both surfaces mirror the same backend constraints (`AgentPatchRequest` /
 * `ToolkitUpdateRequest`): a non-empty name capped at 255, and an optional
 * description capped at 1024. They previously carried identical private copies
 * of these helpers; centralising them here keeps the caps and the exact error
 * copy in one place so the two dialogs can't drift.
 */

/** Max characters for an entity name (backend `max_length=255`). */
export const NAME_MAX_LENGTH = 255;

/** Max characters for an entity description (backend `max_length=1024`). */
export const DESCRIPTION_MAX_LENGTH = 1024;

/**
 * Name constraint: non-empty (after trimming) and ≤ 255. Returns an error
 * message or `null` when valid. The server bounds the length but has no
 * `min_length`, so we reject an empty name client-side (clearly a mistake) to
 * give the operator real feedback instead of a silent round-trip.
 */
export function validateName(next: string): string | null {
	const trimmed = next.trim();
	if (trimmed.length === 0) return "Name can't be empty.";
	if (trimmed.length > NAME_MAX_LENGTH) return 'Name must be 255 characters or fewer.';
	return null;
}

/** Description constraint: optional, ≤ 1024. Returns an error message or `null`. */
export function validateDescription(next: string): string | null {
	if (next.length > DESCRIPTION_MAX_LENGTH)
		return 'Description must be 1024 characters or fewer.';
	return null;
}
