# Messaging folders

Enable named instances in `loom/config.toml`:

```toml
connectors = ["home-tg=telegram", "work-tg=telegram"]
```

`home-tg` is the address namespace (`channel:home-tg/42`), sender prefix
(`home-tg:42` in `self/people/<person>/channels.md`) and dedupe prefix.
`telegram` is the code folder, resolved home-first. Each instance receives its
own `BRNRD_STATE` under `loom/connectors/<name>/state` and `BRNRD_CHANNEL` name.
The older `connectors = ["relay"]` keeps its platform addresses unchanged.

Run `brnrd setup channel --home ~/brnrd` (or `brnrd setup --home ~/brnrd`).
Choose a kind and name, supply any folder-specific credential, then send the
shown code to the bot in a direct message. Setup sends `heard you as <id>`
through the same `out` executable, saves your authenticated identity under
your person, and enables the channel only after that reply succeeds.
`--kind`, `--name`, `--person` and `--timeout` can replace those prompts.
This loom command is available explicitly while the daemon's main help stays
on its existing surface until cut-over; `brnrd setup --help` lists its options.

Telegram setup accepts a BotFather token or a `command:`, `file:` or `env:`
secret source. It stores the source or token in the instance's `token` file
(mode 0600), and resolves a source anew for each API use. Use a bot without an
active webhook or another poller. No live Bot API request is made by the tests.
They run the folder against a local fake server instead.

Relay stays on its existing account/browser setup (`brnrd account connect`)
and plain `connectors = ["relay"]` declaration, with `channel:telegram/<chat>`
and `channel:whatsapp/<chat>` addresses. `name=relay` is refused by both config
and channel setup: a multi-platform folder cannot take one name yet.

Folder contract: `in <drop-dir>` publishes one complete letter by rename,
after any attachments in `<id>.d/`; `out <letter-file>` prints `{"ids": [...]}`
and exits 0 for sent, 3 for refused before sending, other for maybe-sent.
`carries` can be empty, or contain `edit` if `out` accepts `edit: <message-id>`.
Legacy platform lines remain readable for old `connectors` lists. The optional
`setup` executable inherits the same environment and owns its own questions.
