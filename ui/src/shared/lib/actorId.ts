/**
 * A compact form of an opaque actor id for when no name is known: the type
 * prefix, an ellipsis and the last four characters (`agnt_…e4d9`), so two
 * unresolved ids still read apart without the whole KSUID taking the line.
 * An id short enough to show whole is returned as it is.
 */
export function shortActorId(id: string): string {
	const underscore = id.indexOf('_');
	const prefix = underscore >= 0 ? id.slice(0, underscore + 1) : '';
	const body = id.slice(prefix.length);
	if (body.length <= 8) return id;
	return `${prefix}…${body.slice(-4)}`;
}
