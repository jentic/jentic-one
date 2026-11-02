/**
 * The self-registration command an operator pastes where their agent runs.
 *
 * Raw `POST /register` needs an Ed25519 JWKS no one can type by hand, so the
 * snippet is the CLI, which makes the keypair and performs the registration in
 * one step. Every surface that shows `jentic register` builds it here, so the
 * quoting rule and the flags are defined once.
 */

/** Characters a POSIX shell passes through unquoted and unexpanded. */
const SHELL_SAFE = /^[A-Za-z0-9._/:@-]+$/;

/**
 * One shell word for `value`. Anything outside the safe set is single-quoted
 * (a `'` inside becomes `'\''`), so no `$()`, backtick, `\` or glob in an
 * agent name or a server-reported URL can run or expand when pasted.
 */
export function shellArg(value: string): string {
	return SHELL_SAFE.test(value) ? value : `'${value.replace(/'/g, `'\\''`)}'`;
}

/** How a command word is coloured when displayed. */
export type CommandTone = 'program' | 'plain' | 'flag' | 'url' | 'value' | 'placeholder';

export interface CommandToken {
	/** Exactly what is copied for this word (already shell-quoted). */
	text: string;
	tone: CommandTone;
}

/** The placeholder shown for a broker URL the instance can't advertise.
 * Single-quoted: bare, a shell reads `<broker-url>` as a redirection from a
 * file named `broker-url`, so a command pasted unedited would fail on that
 * rather than on the CLI's own check of the URL. */
const BROKER_URL_PLACEHOLDER = "'<broker-url>'";

export interface RegisterCommandOptions {
	/** The instance's control-plane URL (`--url`, never `--base-url` — #1204). */
	url: string;
	/** `--name`; omitted when absent. */
	name?: string;
	/** `--broker-url`: a string is the flag's value, `null` the placeholder the
	 * operator must fill in, and absent omits the flag. */
	brokerUrl?: string | null;
	/** `setup` registers the same way, then wires the agent's MCP clients. */
	verb?: 'register' | 'setup';
}

/** `jentic register` (or `setup`) as words, for a display that colours what it copies. */
export function registerCommandTokens({
	url,
	name,
	brokerUrl,
	verb = 'register',
}: RegisterCommandOptions): CommandToken[] {
	const tokens: CommandToken[] = [
		{ text: 'jentic', tone: 'program' },
		{ text: verb, tone: 'plain' },
		{ text: '--url', tone: 'flag' },
		{ text: shellArg(url), tone: 'url' },
	];
	if (brokerUrl !== undefined) {
		tokens.push(
			{ text: '--broker-url', tone: 'flag' },
			brokerUrl === null
				? { text: BROKER_URL_PLACEHOLDER, tone: 'placeholder' }
				: { text: shellArg(brokerUrl), tone: 'url' },
		);
	}
	if (name !== undefined) {
		tokens.push({ text: '--name', tone: 'flag' }, { text: shellArg(name), tone: 'value' });
	}
	return tokens;
}

/** The copyable command line for `tokens`. */
export function commandText(tokens: CommandToken[]): string {
	return tokens.map((t) => t.text).join(' ');
}

/** `jentic register` for an instance, as one command line. */
export function registerCommand(options: RegisterCommandOptions): string {
	return commandText(registerCommandTokens(options));
}

/** The most characters `register --name` / `setup --name` accept. */
export const REGISTER_NAME_MAX_LENGTH = 64;

/** `register --name`'s own check: lowercase letters, digits and hyphens, a
 * letter or digit first, up to 64 — a name the CLI refuses never reaches the
 * server, so the field must refuse it first. */
const REGISTER_NAME = /^[a-z0-9][a-z0-9-]{0,63}$/;

/** Why `name` can't go on a `register`/`setup` command, or `null` when it can. */
export function registerNameError(name: string): string | null {
	const trimmed = name.trim();
	if (!trimmed) return 'A name is required.';
	if (REGISTER_NAME.test(trimmed)) return null;
	const slug = configName(trimmed);
	return `The CLI takes lowercase letters, digits and hyphens only, up to ${REGISTER_NAME_MAX_LENGTH}${
		slug ? ` — try ${slug}` : ''
	}.`;
}

/** The CLI's config-name rule (`config.SanitizeName`): lowercase, `[a-z0-9-]`, ≤ 64.
 * Its output also passes `register --name`'s check (`^[a-z0-9][a-z0-9-]{0,63}$`). */
export function configName(s: string): string {
	return s
		.trim()
		.toLowerCase()
		.replace(/[^a-z0-9-]+/g, '-')
		.replace(/^-+|-+$/g, '')
		.slice(0, 64)
		.replace(/-+$/, '');
}

/** The environment name `jentic register`/`setup` derive for an instance URL:
 * the host's first label, with `localhost` read as `127.0.0.1`
 * (`cmdcore/register_derive.go`); `default` when there is no host to read. */
export function cliEnvName(url: string): string {
	let host = '';
	try {
		host = new URL(url).hostname.replace(/^\[|\]$/g, '');
	} catch {
		// A scheme-less URL has no host to the CLI either.
	}
	if (host === 'localhost') host = '127.0.0.1';
	return configName(host.split('.')[0]) || 'default';
}

/** The context `jentic register`/`setup` creates: `<env>-<name>`. */
export function cliContextName(url: string, name: string): string {
	return configName(`${cliEnvName(url)}-${name}`);
}

/** Stands in for the key itself in a command an operator pastes. */
export const API_KEY_PLACEHOLDER = '<api-key>';

/**
 * The commands that give an agent CREATED HERE a CLI identity: `jentic
 * register` would register a second agent, so its API key is stored as the
 * identity instead — the environment for this instance, the identity holding
 * the key, and the context `jentic mcp --context` pins (named as `register`
 * would name it, {@link cliContextName}). `apiKey` defaults to a placeholder.
 */
export function apiKeyWiringCommands(options: {
	url: string;
	brokerUrl?: string | null;
	name: string;
	apiKey?: string;
}): string[] {
	const { url, brokerUrl, name, apiKey = API_KEY_PLACEHOLDER } = options;
	const env = cliEnvName(url);
	const broker =
		brokerUrl === undefined
			? ''
			: ` --broker-url ${brokerUrl === null ? BROKER_URL_PLACEHOLDER : shellArg(brokerUrl)}`;
	return [
		`jentic env add ${shellArg(env)} --url ${shellArg(url)}${broker}`,
		`jentic identity add ${shellArg(name)} --env ${shellArg(env)} --api-key ${shellArg(apiKey)}`,
		`jentic context create ${shellArg(cliContextName(url, name))} --env ${shellArg(env)} --identity ${shellArg(name)}`,
	];
}
