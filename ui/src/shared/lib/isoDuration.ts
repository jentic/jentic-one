/**
 * ISO 8601 durations, as the API carries a binding's idle limit
 * (`max_idle_period`, `requested_max_idle_period`): `"P14D"`, `"PT36H"`,
 * `"P2W"`, `"P1DT12H"`. The console works in whole seconds and converts at
 * the API boundary. Only fixed-length units: weeks, days, hours, minutes and
 * seconds (never years or months).
 */
const DAY = 86_400;
const UNIT_SECONDS: Record<string, number> = { W: 7 * DAY, D: DAY, H: 3_600, M: 60, S: 1 };
const DURATION =
	/^P(?:(?<W>\d+)W)?(?:(?<D>\d+)D)?(?:T(?=\d)(?:(?<H>\d+)H)?(?:(?<M>\d+)M)?(?:(?<S>\d+)S)?)?$/;

/** Whole seconds in an ISO 8601 duration; null for none or one it cannot read. */
export function isoDurationSeconds(value: string | null | undefined): number | null {
	if (!value) return null;
	const match = DURATION.exec(value.trim().toUpperCase());
	const groups = match?.groups;
	if (!groups || !Object.values(groups).some(Boolean)) return null;
	return Object.entries(groups).reduce(
		(total, [unit, n]) => total + (n ? Number(n) * UNIT_SECONDS[unit] : 0),
		0,
	);
}

/** The shortest ISO 8601 duration for whole seconds (`1209600` → `"P14D"`). */
export function isoDuration(seconds: number): string {
	const days = Math.floor(seconds / DAY);
	let rest = seconds % DAY;
	const hours = Math.floor(rest / 3_600);
	rest %= 3_600;
	const minutes = Math.floor(rest / 60);
	const secs = rest % 60;
	const time = [
		[hours, 'H'],
		[minutes, 'M'],
		[secs, 'S'],
	]
		.filter(([n]) => n)
		.map(([n, u]) => `${n}${u}`)
		.join('');
	if (!days && !time) return 'PT0S';
	return `P${days ? `${days}D` : ''}${time ? `T${time}` : ''}`;
}

/** {@link isoDuration} that keeps null (and undefined) as they are. */
export function isoDurationOrNull<T extends null | undefined>(seconds: number | T): string | T {
	return typeof seconds === 'number' ? isoDuration(seconds) : seconds;
}
