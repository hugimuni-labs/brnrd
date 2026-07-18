import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';

export default defineConfig({
  site: 'https://gurio.github.io',
  base: '/brr',
  trailingSlash: 'always',
  integrations: [
    starlight({
      title: 'brnrd',
      description: 'Local coding agents, reachable from anywhere, with continuity across runs.',
      logo: {
        src: './src/assets/sigil.svg',
        alt: 'brnrd',
      },
      favicon: '/favicon.svg',
      editLink: {
        baseUrl: 'https://github.com/Gurio/brr/edit/main/docs/src/content/docs/',
      },
      social: [
        { icon: 'github', label: 'GitHub', href: 'https://github.com/Gurio/brr' },
      ],
      customCss: ['./src/styles/brnrd.css'],
      lastUpdated: true,
      credits: true,
      sidebar: [
        { label: 'Home', slug: 'index' },
        {
          label: 'Getting started',
          items: [
            { label: '1. Install', slug: 'getting-started/install' },
            { label: '2. Connect', slug: 'getting-started/connect' },
            { label: '3. First task', slug: 'getting-started/first-task' },
          ],
        },
        {
          label: 'Concepts',
          items: [
            { label: 'The resident', slug: 'concepts/resident' },
            { label: 'Runs & environments', slug: 'concepts/environments' },
            { label: 'Gates & authorization', slug: 'concepts/gates' },
          ],
        },
        {
          label: 'Guides',
          items: [
            { label: 'Models & quota', slug: 'guides/models' },
            { label: 'Troubleshooting', slug: 'guides/troubleshooting' },
          ],
        },
        { label: 'Security & privacy', slug: 'security' },
        { label: 'Self-hosting brnrd', slug: 'self-hosting' },
        {
          label: 'Reference',
          items: [{ label: 'CLI', slug: 'reference/cli' }],
        },
      ],
    }),
  ],
});
