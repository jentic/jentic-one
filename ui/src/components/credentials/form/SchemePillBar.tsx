import { Button } from '@/components/ui/Button';
import type { SchemeOption } from '@/lib/credentials/schemes';

/**
 * Pill bar letting the user pick which auth method to use when an API
 * declares more than one in `securitySchemes` (e.g. GitHub: bearer +
 * OAuth2). Hidden by the parent when there's only one option — the
 * caller decides visibility, this component just renders.
 *
 * Pure presentation: parent owns `active` and the change callback.
 */
export interface SchemePillBarProps {
	options: SchemeOption[];
	active: SchemeOption | null;
	onChange: (option: SchemeOption) => void;
}

export function SchemePillBar({ options, active, onChange }: SchemePillBarProps) {
	if (options.length <= 1) return null;
	return (
		<fieldset>
			<legend className="text-muted-foreground mb-1.5 block text-xs">Auth method</legend>
			<div className="flex flex-wrap gap-1.5">
				{options.map((opt) => {
					const isActive = active?.name === opt.name;
					return (
						<Button
							key={opt.name}
							type="button"
							aria-pressed={isActive}
							variant={isActive ? 'primary' : 'outline'}
							size="sm"
							onClick={() => onChange(opt)}
							className={`rounded-lg px-3 py-1.5 text-xs transition-colors ${
								isActive
									? 'font-medium'
									: 'bg-background text-muted-foreground border-border hover:text-foreground'
							}`}
						>
							{opt.label}
						</Button>
					);
				})}
			</div>
		</fieldset>
	);
}
