/**
 * AccessRulesHelp — the one "how rules work" hint for the access editors: a
 * short line under the By action | By path toggle, with the rules of
 * evaluation behind its Details, where rules are written.
 */
import { DetailsLine } from '@/shared/ui';

const ACCESS_RULES_HELP = [
	'Actions you set always win.',
	'Everything else goes to the path rules, in order — the first match decides.',
	'Anything still unmatched is denied.',
] as const;

export function AccessRulesHelp({ testId }: { testId?: string }) {
	return (
		<DetailsLine summary="Actions first, then path rules in order" data-testid={testId}>
			<ul className="space-y-1" data-testid="access-rules-help">
				{ACCESS_RULES_HELP.map((line) => (
					<li key={line}>{line}</li>
				))}
			</ul>
		</DetailsLine>
	);
}
