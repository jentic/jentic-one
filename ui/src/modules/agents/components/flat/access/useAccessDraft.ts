/**
 * The one owner of the Manage access draft: the preset and custom rules being
 * edited, seeded from the saved operator rules; whether they differ from what
 * is saved; what a save would change; and the save itself.
 *
 * Lifecycle (the dialog-state rule): the draft is reseeded when the binding
 * changes, when the saved rules change under an untouched draft, and on a
 * successful save or an explicit discard — never on a mere re-render.
 */
import { useCallback, useMemo, useState } from 'react';
import { cleanPermissionRule, isEmptyAllowRule, type PermissionRuleInput } from '@/shared/ui';
import type { PermissionRule } from '@/shared/credentials/api/vendors-types';
import {
	draftForRules,
	draftRulesForPreset,
	joinCustomRules,
	splitCustomRules,
	type RulesPreset,
} from '@/shared/credentials/lib/accessPresets';
import type { ActionCatalog } from '@/shared/credentials/lib/actionRules';
import { useReplaceAgentBindingPermissions } from '@/modules/agents/api';
import {
	draftChanges,
	keepSiblingActions,
	rulesKey,
	siblingActionIds,
	type DraftChanges,
} from '@/modules/agents/lib/accessDraft';

interface Draft {
	preset: RulesPreset | null;
	customRules: PermissionRule[];
}

export interface AccessDraft extends Draft {
	setPreset: (preset: RulesPreset) => void;
	setCustomRules: (rules: PermissionRule[]) => void;
	/** The draft as it would save (other APIs' actions kept). */
	toSave: PermissionRule[];
	dirty: boolean;
	/** An allow or Ask rule with no condition: the backend refuses it. */
	invalid: boolean;
	changes: DraftChanges;
	discard: () => void;
	save: (onSaved?: () => void) => void;
	saving: boolean;
}

export function useAccessDraft({
	agentId,
	credentialId,
	saved,
	catalog,
}: {
	agentId: string;
	credentialId: string;
	/** The saved operator rules (system rules excluded); undefined while loading. */
	saved: PermissionRule[] | undefined;
	catalog: ActionCatalog;
}): AccessDraft {
	const bindingKey = `${agentId}\n${credentialId}`;
	const savedRules = useMemo(() => saved ?? [], [saved]);
	const savedKey = rulesKey(savedRules);
	const [draft, setDraft] = useState<Draft>(() => draftForRules(savedRules));
	// What the draft was seeded from, and whether the operator has touched it
	// since — an untouched draft follows the saved rules; a touched one is kept.
	const seedKey = saved === undefined ? null : `${bindingKey}\n${savedKey}`;
	const [seededFrom, setSeededFrom] = useState(seedKey);
	const [touched, setTouched] = useState(false);
	const sameBinding = seededFrom?.startsWith(`${bindingKey}\n`) ?? false;
	if (seedKey !== null && seedKey !== seededFrom && (!touched || !sameBinding)) {
		// Adjusting state while rendering (React's derived-state pattern): the
		// draft never renders a frame against the wrong saved rules.
		setSeededFrom(seedKey);
		setTouched(false);
		setDraft(draftForRules(savedRules));
	}

	const keep = useMemo(() => siblingActionIds(savedRules, catalog), [savedRules, catalog]);
	// Custom rules save in one order — action choices first, then the path
	// rules — so an older order is put right here, and shows as a pending change.
	const toSave = useMemo(() => {
		const { pathRules, groups } = splitCustomRules(draft.customRules);
		const ordered =
			draft.preset === 'custom' ? joinCustomRules(pathRules, groups) : draft.customRules;
		return keepSiblingActions(draftRulesForPreset(draft.preset, ordered), savedRules, keep);
	}, [draft.preset, draft.customRules, savedRules, keep]);
	const dirty = rulesKey(toSave) !== savedKey;

	const replace = useReplaceAgentBindingPermissions(agentId, credentialId);
	const discard = useCallback(() => {
		setTouched(false);
		setDraft(draftForRules(savedRules));
	}, [savedRules]);
	const invalid = toSave.some((r) => isEmptyAllowRule(r as unknown as PermissionRuleInput));
	const changes = useMemo(
		() => draftChanges(savedRules, toSave, catalog),
		[savedRules, toSave, catalog],
	);

	return {
		...draft,
		setPreset: (preset) => {
			setTouched(true);
			setDraft((d) => ({ ...d, preset }));
		},
		setCustomRules: (customRules) => {
			setTouched(true);
			setDraft((d) => ({ ...d, customRules }));
		},
		toSave,
		dirty,
		invalid,
		changes,
		discard,
		save: (onSaved) => {
			if (!dirty || invalid) return;
			const body = toSave.map((r) =>
				cleanPermissionRule(r as unknown as PermissionRuleInput),
			);
			replace.mutate(body, {
				// The saved read is seeded from the PUT's echo, so the reseed above
				// lands the draft on exactly what was stored.
				onSuccess: () => {
					setTouched(false);
					onSaved?.();
				},
			});
		},
		saving: replace.isPending,
	};
}
