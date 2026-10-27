import { describe, it, expect } from 'vitest';
import {
	cliContextName,
	commandText,
	registerCommand,
	registerCommandTokens,
	shellArg,
} from '@/modules/agents/lib/registerCommand';

describe('shellArg', () => {
	it.each([
		['plain name', 'research-bot', 'research-bot'],
		['URL', 'https://jentic.example.test:8443/x', 'https://jentic.example.test:8443/x'],
		['spaces', 'research bot two', "'research bot two'"],
		['command substitution', '$(rm -rf ~)', "'$(rm -rf ~)'"],
		['backtick', 'a`id`b', "'a`id`b'"],
		['trailing backslash', 'bot\\', "'bot\\'"],
		['single quote', "it's", "'it'\\''s'"],
		['empty', '', "''"],
	])('%s', (_label, input, expected) => {
		expect(shellArg(input)).toBe(expected);
	});
});

describe('registerCommand', () => {
	it('builds --url, then --broker-url, then --name', () => {
		expect(registerCommand({ url: 'https://j.test', name: 'my bot' })).toBe(
			"jentic register --url https://j.test --name 'my bot'",
		);
		expect(registerCommand({ url: 'https://j.test', brokerUrl: 'https://b.test' })).toBe(
			'jentic register --url https://j.test --broker-url https://b.test',
		);
		expect(registerCommand({ url: 'https://j.test', brokerUrl: null })).toBe(
			"jentic register --url https://j.test --broker-url '<broker-url>'",
		);
	});

	it('the displayed tokens are exactly what is copied', () => {
		const tokens = registerCommandTokens({ url: 'https://j.test/$(id)', name: "x'y" });
		expect(tokens.map((t) => t.tone)).toEqual([
			'program',
			'plain',
			'flag',
			'url',
			'flag',
			'value',
		]);
		expect(commandText(tokens)).toBe(
			"jentic register --url 'https://j.test/$(id)' --name 'x'\\''y'",
		);
	});

	it('`setup` is the same command with the other verb', () => {
		// So a surface showing both snippets never rewrites one into the other by string replacement.
		const tokens = registerCommandTokens({ url: 'https://j.test', name: 'bot', verb: 'setup' });
		expect(commandText(tokens)).toBe('jentic setup --url https://j.test --name bot');
		expect(tokens[1]).toEqual({ text: 'setup', tone: 'plain' });
		expect(registerCommandTokens({ url: 'https://j.test', name: 'bot' })[1].text).toBe(
			'register',
		);
	});
});

/** `cliContextName` hand-ports the CLI's `config.SanitizeName` naming, so it must agree with Go. */
describe('cliContextName', () => {
	// One case per rule in one test: per-test browser-runner cost once hit the 5s timeout.
	it('derives the env from the URL host, as the CLI does', () => {
		expect(cliContextName('https://jentic.example.test', 'bot')).toBe('jentic-bot');
		expect(cliContextName('https://acme-eu.jentic.com:8443/api', 'bot')).toBe('acme-eu-bot');
		// `localhost` is rewritten to 127.0.0.1 before the env is derived.
		expect(cliContextName('http://localhost:8080', 'bot')).toBe('127-bot');
		expect(cliContextName('http://127.0.0.1:8080', 'bot')).toBe('127-bot');
		// Go's `Hostname()` drops an IPv6 literal's brackets.
		expect(cliContextName('http://[::1]:8080', 'bot')).toBe('1-bot');
		expect(cliContextName('http://[2001:db8::7]/x', 'bot')).toBe('2001-db8-7-bot');
	});

	it('falls back to `default` when there is no host to read', () => {
		expect(cliContextName('jentic.example.test', 'bot')).toBe('default-bot');
		expect(cliContextName('', 'bot')).toBe('default-bot');
		expect(cliContextName('not a url', 'bot')).toBe('default-bot');
		expect(cliContextName('file:///tmp/x', 'bot')).toBe('default-bot');
	});

	it('folds the joined name to the config-name alphabet and its 64-char limit', () => {
		expect(cliContextName('https://Jentic.EXAMPLE.test', 'My Bot')).toBe('jentic-my-bot');
		expect(cliContextName('https://j.test', 'bot!!!v2')).toBe('j-bot-v2');
		expect(cliContextName('https://j.test', '***')).toBe('j');
		expect(cliContextName('https://j.test', 'bot-')).toBe('j-bot');

		const long = cliContextName('https://j.test', 'a'.repeat(100));
		expect(long).toHaveLength(64);
		expect(long).toBe(`j-${'a'.repeat(62)}`);
		// The CLI trims a separator exposed by truncation.
		const exposed = cliContextName('https://j.test', `${'a'.repeat(61)} tail`);
		expect(exposed).toHaveLength(63);
		expect(exposed).toBe(`j-${'a'.repeat(61)}`);
		expect(exposed.endsWith('-')).toBe(false);
	});
});
