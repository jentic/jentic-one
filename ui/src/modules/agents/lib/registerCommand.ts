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
}

/** `jentic register` as words, for a display that colours what it copies. */
export function registerCommandTokens({
	url,
	name,
	brokerUrl,
}: RegisterCommandOptions): CommandToken[] {
	const tokens: CommandToken[] = [
		{ text: 'jentic', tone: 'program' },
		{ text: 'register', tone: 'plain' },
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
