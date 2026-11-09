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
	presetForRules,
	splitCustomRules,
	type RulesPreset,
} from '@/shared/credentials/lib/accessPresets';
import type { ActionCatalog } from '@/shared/credentials/lib/actionRules';
import { useReplaceAgentBindingPermissions } from '@/modules/agents/api';
import {
	awaitingActionCatalog,
	draftChanges,
	keepSiblingActions,
	ownRules,
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
	/** Custom rules with no rule at all — a save would deny every call, which
	 * is a preset's job to say, not an empty list's. */
	empty: boolean;
	/** The saved action ids are still being resolved, so whose they are is
	 * unknown — a save now could drop another API's rules. */
	awaitingCatalog: boolean;
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
	// The draft the saved rules reopen as. A preset is judged on THIS API's
	// rules — another API's action rules on a shared credential ride along via
	// `keepSiblingActions` and must not turn the preset into Custom rules. Custom
	// rules keep the whole list, so the other API's actions stay in view.
	const seed = useMemo(() => {
		const own = ownRules(savedRules, catalog);
		const preset = presetForRules(own);
		if (preset !== null && preset !== 'custom' && own.length !== savedRules.length) {
			return { preset, customRules: [] as PermissionRule[] };
		}
		return draftForRules(savedRules);
	}, [savedRules, catalog]);
	const [draft, setDraft] = useState<Draft>(seed);
	// What the draft was seeded from, and whether the operator has touched it
	// since — an untouched draft follows the saved rules; a touched one is kept.
	const seedKey =
		saved === undefined ? null : `${bindingKey}\n${seed.preset}\n${rulesKey(seed.customRules)}`;
	const [seededFrom, setSeededFrom] = useState(seedKey);
	const [touched, setTouched] = useState(false);
	const sameBinding = seededFrom?.startsWith(`${bindingKey}\n`) ?? false;
	if (seedKey !== null && seedKey !== seededFrom && (!touched || !sameBinding)) {
		// Adjusting state while rendering (React's derived-state pattern): the
		// draft never renders a frame against the wrong saved rules.
		setSeededFrom(seedKey);
		setTouched(false);
		setDraft(seed);
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
	// Only an edit makes the draft dirty: the canonical order a save would put
	// an older list in is not the operator's change, so an untouched draft has
	// nothing to save and nothing to guard on leaving. Once edited, the save
	// carries the reorder too, and the diff says so.
	const dirty = touched && rulesKey(toSave) !== savedKey;

	const replace = useReplaceAgentBindingPermissions(agentId, credentialId);
	const discard = useCallback(() => {
		setTouched(false);
		setDraft(seed);
	}, [seed]);
	const invalid = toSave.some((r) => isEmptyAllowRule(r as unknown as PermissionRuleInput));
	// Custom with nothing in it: the bind flow refuses to bind on it, and a save
	// here would deny every call — Full access's opposite, with no preset saying so.
	const empty = draft.preset === 'custom' && draft.customRules.length === 0;
	const awaitingCatalog = useMemo(
		() => awaitingActionCatalog(savedRules, catalog),
		[savedRules, catalog],
	);
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
		empty,
		awaitingCatalog,
		changes,
		discard,
		save: (onSaved) => {
			if (!dirty || invalid || empty || awaitingCatalog) return;
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
