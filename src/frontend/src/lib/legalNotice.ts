// The single source of truth for /legal-notice (mentions légales, #569 doc 4).
//
// Why a module and not literals in the .svelte file: the statutory identity of
// HugiMuni SAS is not in this repository. Share capital, SIREN, RCS, the
// registered office and the VAT number live on the K-bis the maintainer holds
// (#672 doc 10). A guessed identifier on a page whose whole purpose is truthful
// identification is the worst outcome available here — worse than shipping
// nothing — so every unknown is `value: null` and renders as a loud placeholder,
// and `pendingFields()` reads the *same* array the page renders. One fact, one
// home: the check and the page cannot drift, because there is nothing to drift
// between. (The repo has paid for the other arrangement four times this week —
// #674, #675, #680.)
//
// To fill the page: replace `value: null` with the value from the K-bis. Nothing
// else needs to change; the completeness test in `legalNotice.test.ts` goes green
// on its own, and the footer link appears (see `Landing.svelte`).

/**
 * What a reader sees where a statutory value is still missing. Deliberately not
 * an empty string, an em dash, or anything that could pass for data.
 */
export const PENDING = '⟨à compléter⟩';

export type LegalBlock = 'publisher' | 'host';

export interface LegalField {
	/** Stable id — used by the completeness report and the maintainer checklist. */
	readonly key: string;
	/** Label rendered on the page (French — see the language note below). */
	readonly fr: string;
	/** English gloss. Used by the failure message and the maintainer checklist. */
	readonly en: string;
	readonly block: LegalBlock;
	/** Where the value is read from, for the maintainer's five-minute pass. */
	readonly whereToFind?: string;
	/** `null` = not supplied yet. Anything else must be sourced, never guessed. */
	readonly value: string | null;
	/** Provenance of a stated value. Every non-null `value` carries one. */
	readonly source?: string;
}

/**
 * Publisher block, ordered as the fields appear on an extrait K-bis, so filling
 * them is a single top-to-bottom read of one document.
 */
const PUBLISHER: readonly LegalField[] = [
	{
		key: 'publisher.name',
		fr: 'Dénomination sociale',
		en: 'Registered company name',
		block: 'publisher',
		whereToFind: 'K-bis — dénomination sociale',
		value: 'HugiMuni SAS',
		source: 'LICENSE; /terms §6'
	},
	{
		key: 'publisher.legalForm',
		fr: 'Forme juridique',
		en: 'Legal form',
		block: 'publisher',
		whereToFind: 'K-bis — forme juridique',
		value: 'Société par actions simplifiée (SAS)',
		source: 'expansion of the "SAS" in the registered name'
	},
	{
		key: 'publisher.shareCapital',
		fr: 'Capital social',
		en: 'Share capital of HugiMuni SAS',
		block: 'publisher',
		whereToFind: 'K-bis — capital social',
		value: '500,00 €',
		source: 'K-bis, supplied by the maintainer 2026-07-24 (evt-…-c4db)'
	},
	{
		key: 'publisher.siren',
		fr: 'SIREN',
		en: 'SIREN number (9 digits)',
		block: 'publisher',
		whereToFind: 'K-bis — numéro d’identification / SIREN',
		value: '104 156 260',
		source: 'K-bis, supplied 2026-07-24; Luhn checksum verified'
	},
	{
		key: 'publisher.rcs',
		fr: 'Immatriculation au RCS',
		en: 'RCS registration — registry city plus number, e.g. "RCS Paris 123 456 789"',
		block: 'publisher',
		whereToFind: 'K-bis — greffe du tribunal de commerce + numéro RCS',
		value: 'RCS Tarascon 104 156 260',
		source: 'K-bis, supplied 2026-07-24'
	},
	{
		key: 'publisher.registeredOffice',
		fr: 'Siège social',
		en: 'Registered office — full postal address',
		block: 'publisher',
		whereToFind: 'K-bis — adresse du siège social',
		value: '6 rue de la Verdière, 13200 Arles, France',
		source: 'K-bis, supplied 2026-07-24'
	},
	{
		key: 'publisher.vat',
		fr: 'Numéro de TVA intracommunautaire',
		en: 'Intra-community VAT number, or the word "none" if the company has not been issued one',
		block: 'publisher',
		whereToFind:
			'not on the K-bis — the mémento fiscal / impots.gouv.fr professional account (it is FR + a 2-digit key + the SIREN, but the key must be read, not computed by hand)',
		value: 'FR 73 104 156 260',
		source: 'mémento fiscal, supplied 2026-07-24; key 73 cross-checked against (12 + 3·(SIREN mod 97)) mod 97 — read from the document, the check only confirms it'
	},
	{
		key: 'publisher.publicationDirector',
		fr: 'Directrice de la publication',
		en: 'Director of publication',
		block: 'publisher',
		whereToFind: 'K-bis — président; confirm the spelling of the name before publishing',
		value: 'Alexandra Lapunova, Présidente de HugiMuni SAS',
		source: '#569 (“publication director: Alexandra Lapunova, President of HugiMuni SAS”)'
	},
	{
		key: 'publisher.managingDirector',
		fr: 'Contact technique',
		en: 'Technical contact (the Directeur Général, per #569)',
		block: 'publisher',
		whereToFind: 'K-bis — directeur général; confirm the spelling of the name before publishing',
		value: 'Arseni Lapunov, Directeur Général de HugiMuni SAS',
		source: '#569 (“technical contact: Arseni Lapunov, Directeur Général of HugiMuni SAS”)'
	},
	{
		key: 'publisher.email',
		fr: 'Adresse électronique',
		en: 'Public contact email — a role address, not a personal one',
		block: 'publisher',
		whereToFind:
			'maintainer’s choice; the hugimuni.fr domain already publishes security@hugimuni.fr (SECURITY.md), so this is likely contact@hugimuni.fr',
		value: 'alexandra@hugimuni.fr',
		source: 'supplied 2026-07-24; the company address in the professional tax documentation'
	},
	{
		key: 'publisher.phone',
		fr: 'Téléphone',
		en: 'Telephone number — LCEN art. 6-III-1-b requires one for a legal person, so this is not optional',
		block: 'publisher',
		whereToFind:
			'maintainer’s choice — a number that reaches HugiMuni SAS. NOT the SIE: the value offered 2026-07-24, 04 90 99 12 60, is the switchboard of the Service des impôts des entreprises de Tarascon (verified against lannuaire.service-public.gouv.fr). LCEN art. 6-III-1-b wants a line to the publisher, and publishing a tax office’s number as the company’s own is both a false statement of contact details and an unasked-for redirection of the public onto a government office.',
		value: '+33 6 85 74 01 04',
		source:
			'supplied by the maintainer 2026-07-24 (evt-1784908134204366478-bjgm), after the first candidate was identified as the tax office’s line'
	}
];

/**
 * Host block. Every value here is public and sourced; none of it is guessed.
 * brnrd.dev is deployed to Upsun (`.upsun/config.yaml`), which is the commercial
 * brand of Platform.sh SAS.
 */
const HOST: readonly LegalField[] = [
	{
		key: 'host.name',
		fr: 'Dénomination sociale',
		en: 'Hosting provider — registered name',
		block: 'host',
		value: 'Platform.sh SAS (Upsun)',
		source:
			'upsun.com/trust-center/legal/impressum/; annuaire-entreprises.data.gouv.fr (SIREN 521 496 059)'
	},
	{
		key: 'host.registration',
		fr: 'Immatriculation',
		en: 'Hosting provider — registration',
		block: 'host',
		value: 'RCS Paris B 521 496 059',
		source: 'upsun.com/trust-center/legal/impressum/'
	},
	{
		key: 'host.registeredOffice',
		fr: 'Siège social',
		en: 'Hosting provider — registered address',
		block: 'host',
		value: '22 rue de Palestro, 75002 Paris, France',
		source: 'upsun.com/trust-center/legal/impressum/; annuaire-entreprises.data.gouv.fr'
	},
	{
		key: 'host.phone',
		fr: 'Téléphone',
		en: 'Hosting provider — telephone',
		block: 'host',
		value: '+33 (0)1 40 09 30 00',
		source: 'upsun.com/trust-center/legal/impressum/'
	},
	{
		key: 'host.website',
		fr: 'Site',
		en: 'Hosting provider — website',
		block: 'host',
		value: 'https://upsun.com',
		source: 'upsun.com/trust-center/legal/impressum/'
	}
];

export const LEGAL_NOTICE_FIELDS: readonly LegalField[] = [...PUBLISHER, ...HOST];

export function fieldsIn(
	block: LegalBlock,
	fields: readonly LegalField[] = LEGAL_NOTICE_FIELDS
): readonly LegalField[] {
	return fields.filter((field) => field.block === block);
}

/** What the page shows for a field: the value, or the visible placeholder. */
export function displayValue(field: LegalField): string {
	return field.value ?? PENDING;
}

export function isPending(field: LegalField): boolean {
	return field.value === null;
}

/** Every field still waiting on a value, in K-bis order. */
export function pendingFields(
	fields: readonly LegalField[] = LEGAL_NOTICE_FIELDS
): readonly LegalField[] {
	return fields.filter(isPending);
}

export function isComplete(fields: readonly LegalField[] = LEGAL_NOTICE_FIELDS): boolean {
	return pendingFields(fields).length === 0;
}

/**
 * The maintainer's todo, generated rather than remembered: what is missing, what
 * it is, and which document to read it off. Empty string when nothing is
 * pending, so callers can treat it as the whole failure message.
 */
export function pendingReport(fields: readonly LegalField[] = LEGAL_NOTICE_FIELDS): string {
	const pending = pendingFields(fields);
	if (pending.length === 0) return '';
	const lines = pending.map((field) => {
		const where = field.whereToFind ? `\n      ↳ ${field.whereToFind}` : '';
		return `  • ${field.key} — ${field.en}${where}`;
	});
	return [
		`/legal-notice is incomplete: ${pending.length} statutory field(s) still render "${PENDING}".`,
		'Fill them in src/frontend/src/lib/legalNotice.ts (values are on the K-bis of HugiMuni SAS):',
		...lines,
		'',
		'Until then this page must not be published: LCEN art. 6-III requires the information,',
		'and a page that identifies the publisher incorrectly is worse than no page at all.'
	].join('\n');
}
