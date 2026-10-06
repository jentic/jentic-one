/**
 * The name typed for a `jentic register` command, seeded with a suggestion no
 * agent in the roster has (`suggestAgentName`).
 *
 * Until the operator types, the field shows the suggestion. It follows the
 * roster while that is still loading, then settles once the whole roster is
 * read and holds from there: an arrival carrying the suggested name must not
 * move the command's name out from under it (the arrival is picked by that
 * name). Back to listening (the arrival was denied, and still holds the name)
 * it settles afresh. A name the operator typed — even one they cleared — is
 * theirs and is never replaced. `reset` drops both, for a surface that starts
 * each opening fresh.
 */
import { useCallback, useMemo, useState } from 'react';
import {
	agentNameError,
	duplicateAgentName,
	suggestAgentName,
} from '@/modules/agents/lib/agentName';

export interface RegisterNameOptions {
	/** Every agent name in the org, archived and denied ones included. */
	names: readonly string[];
	/** Whether `names` is the whole roster (every page read). */
	rosterRead: boolean;
	/** The name to suggest when free; `suggestAgentName`'s default when absent. */
	base?: string;
	/** Whether the command is on screen (no arrival yet). */
	listening?: boolean;
}

export function useRegisterName({
	names,
	rosterRead,
	base,
	listening = true,
}: RegisterNameOptions) {
	const [typed, setTyped] = useState<string | null>(null);
	const [settled, setSettled] = useState<string | null>(null);
	const [wasListening, setWasListening] = useState(listening);
	if (listening !== wasListening) {
		setWasListening(listening);
		if (listening) setSettled(null);
	}
	const live = useMemo(() => suggestAgentName(names, base), [names, base]);
	// Settled during render, so the frame the roster completes already shows it.
	if (settled == null && rosterRead) setSettled(live);
	const suggestion = settled ?? live;
	const name = typed ?? suggestion;
	const duplicateOf = useMemo(() => duplicateAgentName(names, name), [names, name]);
	const reset = useCallback(() => {
		setTyped(null);
		setSettled(null);
	}, []);
	return {
		/** What the name field shows. */
		name,
		setName: setTyped as (name: string) => void,
		/** The name the displayed command registers with: the field's, or the
		 * suggestion while the field is blank. */
		commandName: agentNameError(name) ? suggestion : name.trim(),
		/** The existing agent name the field duplicates, or `null`. */
		duplicateOf,
		reset,
	};
}
