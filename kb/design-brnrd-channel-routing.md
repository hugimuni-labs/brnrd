# brnrd channel routing

Status: active design after [`decision-brnrd-repo-first-model.md`](decision-brnrd-repo-first-model.md)

## Shape

External gates are account-owned, but work is repo-routed.

```text
Account
  owns GitHub login, Telegram identity, billing, and connected gates

Repo
  owns code context and the local brr work target

ChannelRoute
  maps an account channel to an active repo
```

This keeps the product honest: users connect their own channels and route prompts into repositories that have local brr agents.

## Telegram

Telegram binds to the brnrd account first. A chat or topic then chooses an active repo.

First reliable UX:

```text
/repos
/repo Gurio/brr
/status
```

A free-form Telegram prompt routes to the selected repo for that chat/topic. If no active repo is selected, brnrd should ask the user to choose one instead of guessing.

Smart routing can be added later, but explicit active repo selection is the safe base.

## GitHub

GitHub-originated events already carry repository identity.

```text
GitHub issue / PR comment
  -> repo_full_name
  -> Repo
  -> enqueue event for that repo
```

The user-facing trigger remains `@brnrd-bot`, with slash/text fallbacks for repos where mention autocomplete is not available yet.

### Callsign autocomplete

GitHub App installation does not make the separate mentionable user account discoverable as a repository participant.

That means `@brnrd-bot` can be a valid brnrd trigger string while still not appearing in GitHub's mention autocomplete for a given repository. The fallback commands (`/brnrd`, `brnrd:`) stay necessary until the bot user is visible to that repo.

Possible future shapes:

```text
Minimum product path
  Keep raw @brnrd-bot matching + /brnrd + brnrd: fallbacks.

Better UX path
  Add an explicit “Invite bot user” action for repo owners/admins.
  This likely requires GitHub administration permission and creates an invitation
  that the bot user/account must be able to accept.

Organization path
  Make brnrd-bot an org member or team member where appropriate,
  then repo visibility/autocomplete becomes a GitHub access-management concern.
```

Do not imply that enabling brnrd for a repo automatically adds the bot user to the repo. Those are separate identities and separate GitHub permission flows.

## Web dashboard

The web dashboard should mirror the same routing model:

```text
Repos
  Gurio/brr
    GitHub: installed
    Telegram: active for chat/topic where applicable
    Local daemon: online/offline
```

The dashboard should not expose `Project` or installation ids in the main path. Debug identifiers can live in an advanced/details view.

## Runtime and dispatch

Runtime is not the durable route. Runtime is selected when the event is dispatched.

A cheap local runner may inspect a task and complete it, defer it, or escalate it to a stronger runtime/model. That behaviour sits after repo routing.

```text
incoming event
  -> account + channel
  -> repo
  -> runtime/model dispatch
```

## Team consequence

Team members should not be modeled as sharing a maintainer's local Codex or Claude. Each developer can run their own local runtime. Shared value comes from repo access, routing policy, channel policy, and shared KB/dominion strategy.
