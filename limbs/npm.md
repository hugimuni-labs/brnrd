# npm

way: command `npm`
key: none here: the package goes up from the release workflow by OIDC, and no body holds an npm login
unlisted: do

| row   | what                                       | how                                        |
|-------|--------------------------------------------|--------------------------------------------|
| look  | view, search, list, whoami                 | passes                                     |
| do    | install, run, test, pack (`npm pack --dry-run` shows what would go up) | passes          |
| ask   | publish a package, a dry run included      | his word naming the package and its version, `name@version` |
| never | unpublish                                  | refused: a person does it on npmjs.com     |

Whatever the small print does not match is `do` (the `unlisted:` line): npm's other acts change this checkout, or need a login no body has. npm takes any unique start of a command (`npm pub` publishes) and options before it (`npm --prefix dir publish`), so the rules match those spellings too, skipping the values of the options they name. A publish's `--dry-run` asks like the upload: npm reads `--dry-run false` as an upload, so the flag's name is not its value. The loom puts a command `npm` in front of the real one, made from this page.

## Small print

```limb
never  npm -*  unp*
  skip: --prefix -C -w --workspace --registry --userconfig --cache --tag --otp --access
  say: brnrd refuses to unpublish; a person does it on npmjs.com

ask publish  npm -*  pu|pub*  +-w|--workspace|--workspaces|--prefix|-C
  skip: --prefix -C -w --workspace --registry --userconfig --cache --tag --otp --access
  target: {call}
  why: a package upload is public and cannot be taken back; this one names its package by workspace or folder option
ask publish  npm -*  pu|pub*  {spec}
  skip: --prefix -C -w --workspace --registry --userconfig --cache --tag --otp --access --provenance-file
  default name: npm pkg get name [--prefix {spec}]
  default version: npm pkg get version [--prefix {spec}]
  take name: "?([^"]*)"?
  take version: "?([^"]*)"?
  target: {name}@{version}
  why: a package upload is public and cannot be taken back

look  npm view|v|info|show|search|ls|list|whoami

e.g. never  npm unpublish brnrd@0.9.1
e.g. never  npm unpub brnrd --force
e.g. never  npm --registry https://registry.npmjs.org unpublish brnrd@0.9.1
e.g. ask    npm publish --access public
e.g. ask    npm pub
e.g. ask    npm publish packaging/npm --tag next
e.g. ask    npm publish -w packaging/npm
e.g. ask    npm --prefix packaging/npm publish
e.g. ask    npm -w pkg pu
e.g. ask    npm publish --dry-run false
e.g. do     npm pack --dry-run
e.g. do     npm ci
e.g. do     npm run build
e.g. do     npm --prefix unpacked run build
e.g. do     npm -g install puppeteer
e.g. look   npm view brnrd versions
e.g. look   npm whoami
```
