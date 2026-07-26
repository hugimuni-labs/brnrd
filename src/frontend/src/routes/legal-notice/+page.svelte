<script lang="ts">
	// Mentions légales — the statutory identification notice required of any
	// French-operated online service by art. 6-III of loi n° 2004-575 (LCEN).
	//
	// Every value on this page comes from `$lib/legalNotice`; nothing is written
	// inline. An identifier that is not yet known renders as a visible
	// placeholder rather than a plausible-looking number, and the completeness
	// test in `$lib/legalNotice.test.ts` stays red until the last one is filled.
	//
	// French, not English, deliberately: this is a French statutory notice, its
	// substance is proper nouns and registration numbers, and the conventional
	// French headings are what a French reader — or the DGCCRF — expects to find.
	//
	// No fetch and no auth: this page must render when everything else is down,
	// and it is readable signed out.
	import { resolve } from '$app/paths';
	import {
		PENDING,
		displayValue,
		fieldsIn,
		isComplete,
		isPending,
		pendingFields
	} from '$lib/legalNotice';

	const publisher = fieldsIn('publisher');
	const host = fieldsIn('host');
	const missing = pendingFields();
	const complete = isComplete();
</script>

<svelte:head><title>Mentions légales · brnrd</title></svelte:head>

<div class="mx-auto max-w-2xl p-6">
	<div class="flex items-start justify-between gap-4">
		<p class="eyebrow">brnrd · mentions légales</p>
		<a
			href={resolve('/')}
			class="font-mono text-[11px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
			>accueil</a
		>
	</div>
	<h1 class="mt-1 font-mono text-2xl font-semibold tracking-tight text-amber-100">
		Mentions légales
	</h1>

	<section class="panel mt-6 p-5">
		<p class="text-sm text-stone-400">
			Informations mises à disposition du public en application de l’article 6-III de la loi n°
			2004-575 du 21 juin 2004 pour la confiance dans l’économie numérique.
		</p>

		{#if !complete}
			<!-- Loud on purpose. If this page ever reaches production while the
			     K-bis values are missing, a reader must see that it is incomplete
			     rather than read a half-filled identity as a complete one. -->
			<div class="mt-5 border border-amber-600 bg-amber-950/50 p-4" role="alert">
				<p class="font-mono text-[11px] tracking-wide text-amber-200 uppercase">
					page incomplète — ne pas publier en l’état
				</p>
				<p class="mt-2 text-sm text-amber-100">
					{missing.length}
					mention(s) obligatoire(s) ne sont pas encore renseignée(s) et s’affichent ci-dessous comme «
					{PENDING} ».
				</p>
			</div>
		{/if}

		<div class="mt-6 space-y-5 text-sm leading-6 text-stone-300">
			<section>
				<h2 class="font-mono text-sm font-semibold tracking-wide text-amber-100">
					1. Éditeur du site
				</h2>
				<dl class="mt-2 space-y-1">
					{#each publisher as field (field.key)}
						<div class="flex flex-wrap gap-x-2">
							<dt class="text-ink-quiet">{field.fr} :</dt>
							<dd>
								{#if isPending(field)}
									<span
										class="border border-amber-600 bg-amber-950/60 px-1 font-mono text-[12px] text-amber-300"
										>{PENDING}</span
									>
								{:else}
									{displayValue(field)}
								{/if}
							</dd>
						</div>
					{/each}
				</dl>
			</section>

			<section>
				<h2 class="font-mono text-sm font-semibold tracking-wide text-amber-100">2. Hébergeur</h2>
				<dl class="mt-2 space-y-1">
					{#each host as field (field.key)}
						<div class="flex flex-wrap gap-x-2">
							<dt class="text-ink-quiet">{field.fr} :</dt>
							<dd>
								{#if isPending(field)}
									<span
										class="border border-amber-600 bg-amber-950/60 px-1 font-mono text-[12px] text-amber-300"
										>{PENDING}</span
									>
								{:else}
									{displayValue(field)}
								{/if}
							</dd>
						</div>
					{/each}
				</dl>
			</section>

			<section>
				<h2 class="font-mono text-sm font-semibold tracking-wide text-amber-100">
					3. Documents liés
				</h2>
				<p class="mt-2">
					<a class="text-sky-400 underline" href={resolve('/terms')}>Conditions d’utilisation</a>
				</p>
			</section>
		</div>
	</section>
</div>
