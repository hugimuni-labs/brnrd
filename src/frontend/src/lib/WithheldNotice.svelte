<script lang="ts">
	import { resolve } from '$app/paths';
	import { withheldCopy, type WithheldLane } from './withheld';

	interface Props {
		withheld: WithheldLane;
	}

	let { withheld }: Props = $props();
</script>

<p class="text-sm text-amber-200">
	{withheldCopy(withheld)} ·
	<a class="underline hover:text-amber-100" href={resolve('/repos')}>set a scope</a>
</p>
