import { beforeEach, describe, expect, it } from 'vitest';
import { page } from 'vitest/browser';
import { renderWithProviders, screen, checkA11y } from '@/__tests__/test-utils';
import { Ledger } from '@/shared/ui';
import type { Credential } from '@/shared/credentials/api';
import type { DiscoveryEntity } from '@/modules/discover/api';
import {
	CatalogApiRow,
	type CatalogApiRowProps,
} from '@/modules/discover/components/CatalogLedgerRows';

const ENTITY: DiscoveryEntity = {
	id: 'github.com',
	apiId: 'github.com',
	summary: 'github.com',
	registered: false,
	updateAvailable: false,
	vendor: 'github.com',
	version: '1.1.4',
	githubUrl: 'https://github.com/jentic/x/github.com',
};

const CREDENTIAL = { credential_id: 'cred_1', name: 'GitHub PAT' } as Credential;

function renderRow(over: Partial<CatalogApiRowProps> = {}) {
	const props: CatalogApiRowProps = {
		entity: ENTITY,
		title: 'GitHub',
		icon: { name: 'GitHub', vendor: 'github.com' },
		vendorLabel: 'github.com',
		versionLabel: '1.1.4',
		flat: false,
		child: false,
		zebra: false,
		selected: false,
		pending: false,
		openHref: null,
		openLabel: 'Open',
		reviewHref: null,
		readyCredentials: [CREDENTIAL],
		dragging: false,
		onOpen: () => {},
		onImport: () => {},
		...over,
	};
	return renderWithProviders(
		<div style={{ width: over.stackStatus ? 360 : 760 }}>
			{/* The ledger's own grouped columns (classes as in CatalogLedger). */}
			<Ledger
				label="API catalog"
				columnsClassName="[--ledger-cols:minmax(0,1fr)_auto] sm:[--ledger-cols:minmax(0,1fr)_minmax(96px,22%)_80px_128px]"
			>
				<CatalogApiRow {...props} />
			</Ledger>
		</div>,
	);
}

/** The topmost element at the centre of `el` (what a mouse click there hits). */
function hitAt(el: Element): Element | null {
	const r = el.getBoundingClientRect();
	return document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
}

describe('CatalogApiRow — "Credential ready" and the row actions', () => {
	beforeEach(async () => {
		await page.viewport(1280, 900);
	});

	it('lets a mouse click on the revealed Add land on Add, not the chip under it', () => {
		// Pins the stacking: a chip with its own z-index would sit over Add.
		renderRow();
		const add = screen.getByTestId('catalog-row-add');
		// Reveal the actions the way hover/focus does.
		add.focus();
		const chip = screen.getByTestId('catalog-row-credential-ready');
		// The two overlap (the actions sit over the status column)…
		expect(chip.getBoundingClientRect().right).toBeGreaterThan(
			add.getBoundingClientRect().left,
		);
		// …and a click at Add's centre lands on Add.
		expect(hitAt(add)?.closest('[data-testid="catalog-row-add"]')).toBe(add);
	});

	it('keeps the chip link clickable while the actions are hidden', () => {
		renderRow();
		const chip = screen.getByTestId('catalog-row-credential-ready');
		expect(hitAt(chip)?.closest('a')).toBe(chip);
	});
});

describe('CatalogApiRow — phones (stacked status)', () => {
	it('puts the status under the name and keeps the actions visible', async () => {
		await page.viewport(390, 844);
		const { container } = renderRow({ stackStatus: true });
		const stacked = screen.getByTestId('catalog-row-status-stacked');
		expect(stacked).toHaveTextContent('Credential ready');
		const name = screen.getByTestId('catalog-row-open-preview');
		expect(stacked.getBoundingClientRect().top).toBeGreaterThan(
			name.getBoundingClientRect().top,
		);
		// The actions don't overlap the stacked status.
		const add = screen.getByTestId('catalog-row-add');
		expect(stacked.getBoundingClientRect().right).toBeLessThanOrEqual(
			add.getBoundingClientRect().left + 1,
		);
		// Fully visible below `sm` — not a mid-fade, low-contrast state.
		const actions = add.closest<HTMLElement>('[data-nodrag]')!;
		expect(getComputedStyle(actions).opacity).toBe('1');
		await checkA11y(container);
		await page.viewport(1280, 900);
	});

	it('shows "Adding…" once (the row status) with no second signal on the button', () => {
		renderRow({ pending: true, readyCredentials: null, stackStatus: true });
		expect(screen.getByTestId('catalog-status-pending')).toHaveTextContent('Adding…');
		expect(screen.queryByTestId('catalog-row-add')).not.toBeInTheDocument();
	});
});
