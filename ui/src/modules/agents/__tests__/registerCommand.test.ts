import { describe, it, expect } from 'vitest';
import {
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
			'jentic register --url https://j.test --broker-url <broker-url>',
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
});
