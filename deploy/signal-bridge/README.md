# Hosted Signal bridge

This is the persistent Signal limb behind brnrd.dev. Adopters never run it.
The bridge owns Signal protocol state; the existing stateless brnrd.dev
container owns accounts, pairing, routing, events, and replies.

Required deployment values:

- `SIGNAL_BRIDGE_HOST` — DNS name pointed at the Scaleway instance
- `BRNRD_WEBHOOK_URL=https://brnrd.dev/v1/webhooks/signal`
- `BRNRD_SIGNAL_WEBHOOK_SECRET` — shared with the brnrd.dev container
- `BRNRD_SIGNAL_API_TOKEN` — shared with the brnrd.dev container

Run `docker compose up -d`, then register the eSIM through the bridge host's
local Signal API. The `signal-state` volume is the identity: back it up and
never run two signal-cli processes against it concurrently.

Registration is exposed only through the bridge's bearer-authenticated
`/v1/register/<number>` and `/v1/register/<number>/verify/<code>` paths.
The eSIM number is deployment config; the SMS code is transient and is never
stored in brnrd's database or repository.

The brnrd.dev container additionally receives:

- `BRNRD_SIGNAL_API_URL=https://<SIGNAL_BRIDGE_HOST>`
- `BRNRD_SIGNAL_API_TOKEN`
- `BRNRD_SIGNAL_NUMBER=+<country><number>`
- `BRNRD_SIGNAL_WEBHOOK_SECRET`
