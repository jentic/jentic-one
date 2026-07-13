import { useId } from 'react';
import { Input, Label, Select } from '@/shared/ui';
import type { ServerVarDef } from '@/modules/credentials/api';

/**
 * Renders an input per OpenAPI **server variable** (e.g. Atlassian's
 * `{your-domain}` for `https://{your-domain}.atlassian.net`). Mirrors
 * jentic-webapp's `ServerVariablesSection`: enum variables become a dropdown,
 * free-form ones a text input, descriptions become labels.
 *
 * Values live in a `Record<name, value>` owned by the parent. Variables with a
 * spec default are pre-seeded by the parent; required ones (no default) gate
 * submit.
 */
export interface ServerVariablesSectionProps {
	variables: ServerVarDef[];
	values: Record<string, string>;
	onChange: (name: string, value: string) => void;
	errors?: Record<string, string>;
}

export function ServerVariablesSection({
	variables,
	values,
	onChange,
	errors = {},
}: ServerVariablesSectionProps) {
	const idPrefix = useId();
	if (variables.length === 0) return null;

	return (
		<fieldset className="border-border bg-muted/30 space-y-3 rounded-xl border p-3">
			<legend className="text-foreground px-1 text-xs font-medium">
				Server configuration
			</legend>
			<p className="text-muted-foreground -mt-1 px-1 text-xs leading-snug">
				This API&apos;s URL has placeholders. Fill them so requests hit the right host.
			</p>
			{variables.map((variable) => {
				const value = values[variable.name] ?? variable.default ?? '';
				const label = formatVariableName(variable.name);
				const hasEnum = variable.enum && variable.enum.length > 0;
				const controlId = `${idPrefix}-${variable.name}`;
				const descId =
					variable.description && !errors[variable.name]
						? `${controlId}-desc`
						: undefined;
				return (
					<div key={variable.name} className="space-y-1.5">
						<Label htmlFor={controlId} required={variable.required}>
							{label}
						</Label>
						{hasEnum ? (
							<Select
								id={controlId}
								value={value}
								error={errors[variable.name]}
								aria-describedby={descId}
								onChange={(e): void => onChange(variable.name, e.target.value)}
							>
								{variable.enum!.map((option) => (
									<option key={option} value={option}>
										{option}
										{option === variable.default ? ' (default)' : ''}
									</option>
								))}
							</Select>
						) : (
							<Input
								id={controlId}
								value={value}
								error={errors[variable.name]}
								aria-describedby={descId}
								onChange={(e): void => onChange(variable.name, e.target.value)}
								placeholder={variable.default || `Enter ${variable.name}`}
							/>
						)}
						{variable.description && !errors[variable.name] && (
							<p id={descId} className="text-muted-foreground text-xs leading-snug">
								{variable.description}
							</p>
						)}
					</div>
				);
			})}
		</fieldset>
	);
}

/** `your-domain` / `region_id` → `Your Domain` / `Region Id`. */
function formatVariableName(key: string): string {
	return key
		.replace(/[-_]/g, ' ')
		.replace(/([a-z])([A-Z])/g, '$1 $2')
		.replace(/\b\w/g, (c) => c.toUpperCase());
}
