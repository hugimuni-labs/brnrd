# npm
trigger: npm, npx, npmjs.com, registry.npmjs.org

A note on how a resident reaches npm. It judges nothing; the mark on each call does (threads/judged-acts). The content comes from brnrd#2762, which was closed unmerged when the tables went.

**Way in.** The command `npm`. No body holds an npm login: the brnrd package goes up from the release workflow by OIDC, never from a room. `npm whoami` saying "not logged in" is the normal state, not a block.

**What is final here.** A package upload: it is public, and a version can never be published again. That includes `npm publish --dry-run false`, which npm reads as an upload, because only the flag's name counts, not its value. Unpublishing too: a person does it on npmjs.com. Both cite his letter naming `name@version`.

**Spellings that are an upload.** npm takes any unique start of a command (`npm pub`, `npm pu`) and options before it (`npm --prefix dir publish`, `npm -w pkg pu`). A mark must say `final` for all of them.

**What is a change.** `npm install`, `npm ci`, `npm run …` and `npm pack` change this checkout (and `~/.npm`, a cache). `npm pack --dry-run` shows what would go up without uploading.

**Looks.** `npm view`, `info`, `search`, `ls`, `whoami`. They reach the registry, so they cross the wall with `look`.
