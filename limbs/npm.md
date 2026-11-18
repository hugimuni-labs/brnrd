# npm

way: command `npm`
key: none here: the package goes up from the release workflow by OIDC, and no body holds an npm login
unlisted: do

| row   | what                                       | how                                        |
|-------|--------------------------------------------|--------------------------------------------|
| look  | view, search, list, whoami                 | passes                                     |
| do    | install, run, test, pack, a dry run        | passes                                     |
| ask   | publish a package                          | his word naming the package and its version, `name@version` |
| never | unpublish                                  | refused: a person does it on npmjs.com     |

Whatever the small print does not match is `do` (the `unlisted:` line): npm's other acts change this checkout, or need a login no body has. npm takes any unique start of a command (`npm pub` publishes) and options before it (`npm --prefix dir publish`), so the rules match those spellings too; a call that puts an option first and names a word starting `pu` or `unp` anywhere is read as a publish or an unpublish. The loom puts a command `npm` in front of the real one, made from this page.

## Small print

```limb
never  npm unp*
  say: brnrd refuses to unpublish; a person does it on npmjs.com
never  npm -*  @unp*
  say: brnrd refuses what reads as an unpublish; a person does it on npmjs.com (another call: put its command first)

ask publish  npm pu|pub*  +-w|--workspace|--workspaces  !--dry-run
  target: {call}
  why: a package upload is public and cannot be taken back; this one names its package by workspace
ask publish  npm pu|pub*  {spec}  !--dry-run
  skip: --tag --access --otp --registry --provenance-file
  default name: npm pkg get name [--prefix {spec}]
  default version: npm pkg get version [--prefix {spec}]
  take name: "?([^"]*)"?
  take version: "?([^"]*)"?
  target: {name}@{version}
  why: a package upload is public and cannot be taken back
ask publish  npm -*  @pu*  !--dry-run
  target: {call}
  why: what reads as a package upload, which is public and cannot be taken back (another call: put its command first)

look  npm view|v|info|show|search|ls|list|whoami

e.g. never  npm unpublish brnrd@0.9.1
e.g. never  npm unpub brnrd --force
e.g. never  npm --registry https://registry.npmjs.org unpublish brnrd@0.9.1
e.g. ask    npm publish --access public
e.g. ask    npm pub
e.g. ask    npm publish packaging/npm --tag next
e.g. ask    npm publish -w packaging/npm
e.g. ask    npm --prefix packaging/npm publish
e.g. do     npm publish --dry-run
e.g. do     npm pack
e.g. do     npm ci
e.g. do     npm run build
e.g. do     npm --prefix src/frontend run build
e.g. look   npm view brnrd versions
e.g. look   npm whoami
```
