"""envoy_x.py — the X (Twitter) envoy's mechanics: post, reply, delete, read.

Ported from the account home's ``account/x-post.py`` / ``x-read.py``
(2026-08-13 — see ``buildlog/0001.md``'s postscript: the account-local
script tweeted the literal string ``"--help"`` because argv was payload,
no flag handling. The guards below (``-h``/``--help``, flag-shaped-text
refusal) already existed in the account scripts by the time this module
was written; porting them here with tests is what pins them against a
silent revert — see ``design-the-envoy-as-product.md``, w-14:
machinery-is-product).

Every function here is parameterized by :class:`Paths` — built from a
single account-home directory, or handed explicit files — **never a
hardcoded account**. Same discipline the twin scripts had:

- single-writer token discipline: a 401 shells out to the refresh script
  named in ``Paths.refresh`` and retries once; the access token is never
  printed or logged.
- one receipt line appended to ``Paths.log`` per post, reply, and delete
  (a delete's row carries ``action: "deleted"``) — the envoy's own audit
  trail, so a reader can check the mouth without the platform's
  cooperation.
- dry-run prints the would-be payload and returns before any network call.
- ``-h``/``--help`` prints usage and returns before any network call —
  the regression this module exists to pin.
- text that looks flag-shaped (``args[0].lstrip().startswith("-")``) is
  refused; a leading space is the deliberate escape hatch for text that
  legitimately starts with a dash.
- a reply threads through ``--reply-to <id>``.

The human-readable receipt line prints ``https://x.com/i/status/<id>`` —
X's account-agnostic canonical status link — rather than a hardcoded
handle, since this module has no account identity to hardcode.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

API = "https://api.x.com/2"

POST_USAGE = """\
Usage: envoy-x post "text"                  -> tweet
       envoy-x post "text" --reply-to <id>  -> reply in thread
       envoy-x post "text" --dry-run        -> print what would post
       envoy-x post delete <tweet-id>       -> delete a post
       add --json for the raw API response

Every post appends one line to the receipt log beside the account env
file (what went out, when, in reply to what; a delete appends
action: deleted), so a reader can audit the mouth without the platform's
cooperation.\
"""

READ_USAGE = """\
Usage: envoy-x read           -> mentions since last look + metrics
       envoy-x read --all     -> ignore the since-cursor this once
       envoy-x read --json    -> machine shape\
"""


@dataclass(frozen=True)
class Paths:
    """The account-scoped files this module touches — never hardcoded.

    - ``env`` — the token/secrets file (a ``x_Access_Token=`` line, read
      fresh on every call; the token is never cached in memory beyond one
      call's lifetime).
    - ``log`` — the receipt-trail JSONL, one line appended per post,
      reply, or delete.
    - ``refresh`` — the single-writer refresh script, shelled out to on a
      401 and never otherwise.
    - ``state`` — the read-cursor file (``since_id`` of the newest
      mention seen).
    """

    env: Path
    log: Path
    refresh: Path
    state: Path

    @classmethod
    def in_dir(cls, directory: Path | str) -> "Paths":
        """The four well-known filenames, resolved under *directory*."""
        d = Path(directory)
        return cls(
            env=d / "x-brnrd-resident.env",
            log=d / "x-post-log.jsonl",
            refresh=d / "x-refresh.py",
            state=d / "x-read-state.json",
        )


# ── token + the single-writer refresh lane ───────────────────────────


def token(env_path: Path) -> str:
    """The current access token from *env_path*'s ``x_Access_Token=`` line."""
    for line in open(env_path):
        if line.startswith("x_Access_Token="):
            return line.strip().split("=", 1)[1]
    raise SystemExit("no access token in env file")


def _refresh(refresh_path: Path) -> str:
    """Shell out to the single-writer refresh script; return its fresh token.

    The refresh script owns persisting the rotated pair — this module
    never writes the env file itself.
    """
    return subprocess.run(
        [sys.executable, str(refresh_path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


# ── the wire ──────────────────────────────────────────────────────────


def _request(url: str, tok: str, *, data: bytes | None = None, method: str = "GET") -> Request:
    headers = {"Authorization": f"Bearer {tok}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    return Request(url, data=data, headers=headers, method=method)


def get(
    path: str, params: dict[str, Any], tok: str, paths: Paths, retried: bool = False
) -> tuple[dict[str, Any], str]:
    """``GET`` *path* against the X API; 401 refreshes once and retries.

    Returns ``(body, tok)`` — the (possibly refreshed) token, so a caller
    chaining several ``get`` calls reuses the fresh one instead of
    refreshing again on the next 401.
    """
    url = f"{API}{path}?{urllib.parse.urlencode(params)}" if params else f"{API}{path}"
    req = _request(url, tok)
    try:
        return json.load(urlopen(req)), tok
    except urllib.error.HTTPError as e:
        if e.code == 401 and not retried:
            fresh = _refresh(paths.refresh)
            return get(path, params, fresh, paths, retried=True)
        raise


def post(payload: dict[str, Any], tok: str, paths: Paths, retried: bool = False) -> dict[str, Any]:
    """``POST /tweets``; 401 refreshes once and retries."""
    req = _request(f"{API}/tweets", tok, data=json.dumps(payload).encode(), method="POST")
    try:
        return json.load(urlopen(req))
    except urllib.error.HTTPError as e:
        if e.code == 401 and not retried:
            fresh = _refresh(paths.refresh)
            return post(payload, fresh, paths, retried=True)
        detail = e.read().decode(errors="replace")[:500]
        raise SystemExit(f"post failed: HTTP {e.code} — {detail}")


def delete(tweet_id: str, tok: str, paths: Paths, retried: bool = False) -> dict[str, Any]:
    """``DELETE /tweets/<id>``; 401 refreshes once and retries."""
    req = _request(f"{API}/tweets/{tweet_id}", tok, method="DELETE")
    try:
        return json.load(urlopen(req))
    except urllib.error.HTTPError as e:
        if e.code == 401 and not retried:
            fresh = _refresh(paths.refresh)
            return delete(tweet_id, fresh, paths, retried=True)
        raise SystemExit(
            f"delete failed: HTTP {e.code} — {e.read().decode(errors='replace')[:300]}"
        )


# ── the receipt trail ────────────────────────────────────────────────


def _append_receipt(log_path: Path, record: dict[str, Any]) -> None:
    with open(log_path, "a") as fh:
        fh.write(json.dumps(record) + "\n")


# ── weighted length: what X's 280 actually measures ──────────────────
#
# Measured live 2026-08-15 (see ``kb``/PR body for the full writeup): a
# post that ``len(text) <= 280`` still comes back a fieldless 403 —
# ``{"detail":"You are not permitted to perform this action.","status":403,
# "title":"Forbidden"}`` — because X does not count characters, it counts
# ``twitter-text`` *weight*, and it treats anything link-shaped as a flat
# ``TRANSFORMED_URL_LENGTH`` regardless of how short the token actually is.
# ``x-browser.py`` (12 chars) parses as a link — ``.py`` is Paraguay's
# ccTLD — and is charged 23, not 12: an 11-weight tax invisible to
# ``len()``. This is why every filename this resident writes about
# (``.py``, ``.sh``, ``.md``, ``.io``, ``.dev``, ``.ai`` are all live
# TLDs) was silently at risk.

#: X's own limit is on *weighted* length, not on ``len()``.
MAX_WEIGHTED_LENGTH = 280

#: What X's ``t.co`` wrapper charges for *any* link-shaped token, no
#: matter how short or long the token itself is. (Currently 23; X has
#: changed this number before and will again — there is no live way to
#: read it from the API, so it is a constant to keep in step by hand.)
TRANSFORMED_URL_LENGTH = 23

#: Code points weighted 1 instead of the default 2 — ``twitter-text``'s
#: ``ranges``, ported verbatim (config v3: default weight 200, weight 100
#: in-range, scale 100 — expressed directly here as 2 / 1 so no division
#: step is needed). Covers Latin/Greek/Cyrillic/Armenian/Hebrew/Arabic/
#: Syriac/Thaana/…/Georgian (0–4351) plus a handful of format and
#: punctuation blocks (zero-widths, dashes/quotes, primes).
_WEIGHT_1_RANGES = ((0, 4351), (8192, 8205), (8208, 8223), (8242, 8247))


def _char_weight(codepoint: int) -> int:
    for lo, hi in _WEIGHT_1_RANGES:
        if lo <= codepoint <= hi:
            return 1
    return 2


# gTLD + ccTLD, ASCII labels only, pulled from twitter/twitter-text's own
# ``validGTLD.js`` / ``validCCTLD.js`` (2026-08-15) — the same table X's
# own extractor validates a bare (schemeless) dotted token's last label
# against before deciding it is a link. Deliberately the *upstream* list,
# not a hand-picked "common TLDs" shortlist: a narrower list under-counts
# (misses a real link, reproducing this exact defect); this one only
# over-counts, which the ticket asks to prefer. IDN/non-Latin TLDs (한국,
# 中国, …) are out of scope — see "What this does not verify" below; they
# cannot be reached by this regex's ASCII host-label class regardless of
# whether they're in this set.
_VALID_TLDS = frozenset("""
aaa aarp abarth abb abbott abbvie abc able abogado abudhabi academy
accenture accountant accountants aco active actor ad adac ads adult ae aeg
aero aetna af afamilycompany afl africa ag agakhan agency ai aig aigo airbus
airforce airtel akdn al alfaromeo alibaba alipay allfinanz allstate ally
alsace alstom am americanexpress americanfamily amex amfam amica amsterdam
an analytics android anquan anz ao aol apartments app apple aq aquarelle ar
arab aramco archi army arpa art arte as asda asia associates at athleta
attorney au auction audi audible audio auspost author auto autos avianca aw
aws ax axa az azure ba baby baidu banamex bananarepublic band bank bar
barcelona barclaycard barclays barefoot bargains baseball basketball bauhaus
bayern bb bbc bbt bbva bcg bcn bd be beats beauty beer bentley berlin best
bestbuy bet bf bg bh bharti bi bible bid bike bing bingo bio biz bj bl black
blackfriday blanco blockbuster blog bloomberg blue bm bms bmw bn bnl
bnpparibas bo boats boehringer bofa bom bond boo book booking boots bosch
bostik boston bot boutique box bq br bradesco bridgestone broadway broker
brother brussels bs bt budapest bugatti build builders business buy buzz bv
bw by bz bzh ca cab cafe cal call calvinklein cam camera camp cancerresearch
canon capetown capital capitalone car caravan cards care career careers cars
cartier casa case caseih cash casino cat catering catholic cba cbn cbre cbs
cc cd ceb center ceo cern cf cfa cfd cg ch chanel channel charity chase chat
cheap chintai chloe christmas chrome chrysler church ci cipriani circle
cisco citadel citi citic city cityeats ck cl claims cleaning click clinic
clinique clothing cloud club clubmed cm cn co coach codes coffee college
cologne com comcast commbank community company compare computer comsec
condos construction consulting contact contractors cooking cookingchannel
cool coop corsica country coupon coupons courses cpa cr credit creditcard
creditunion cricket crown crs cruise cruises csc cu cuisinella cv cw cx cy
cymru cyou cz dabur dad dance data date dating datsun day dclk dds de deal
dealer deals degree delivery dell deloitte delta democrat dental dentist
desi design dev dhl diamonds diet digital direct directory discount discover
dish diy dj dk dm dnp do docs doctor dodge dog doha domains doosan dot
download drive dtv dubai duck dunlop duns dupont durban dvag dvr dz earth
eat ec eco edeka edu education ee eg eh email emerck energy engineer
engineering enterprises epost epson equipment er ericsson erni es esq estate
esurance et etisalat eu eurovision eus events everbank exchange expert
exposed express extraspace fage fail fairwinds faith family fan fans farm
farmers fashion fast fedex feedback ferrari ferrero fi fiat fidelity fido
film final finance financial fire firestone firmdale fish fishing fit
fitness fj fk flickr flights flir florist flowers flsmidth fly fm fo foo
food foodnetwork football ford forex forsale forum foundation fox fr free
fresenius frl frogans frontdoor frontier ftr fujitsu fujixerox fun fund
furniture futbol fyi ga gal gallery gallo gallup game games gap garden gay
gb gbiz gd gdn ge gea gent genting george gf gg ggee gh gi gift gifts gives
giving gl glade glass gle global globo gm gmail gmbh gmo gmx gn godaddy gold
goldpoint golf goo goodhands goodyear goog google gop got gov gp gq gr
grainger graphics gratis green gripe grocery group gs gt gu guardian gucci
guge guide guitars guru gw gy hair hamburg hangout haus hbo hdfc hdfcbank
health healthcare help helsinki here hermes hgtv hiphop hisamitsu hitachi
hiv hk hkt hm hn hockey holdings holiday homedepot homegoods homes homesense
honda honeywell horse hospital host hosting hot hoteles hotels hotmail house
how hr hsbc ht htc hu hughes hyatt hyundai ibm icbc ice icu id ie ieee ifm
iinet ikano il im imamat imdb immo immobilien in inc industries infiniti
info ing ink institute insurance insure int intel international intuit
investments io ipiranga iq ir irish is iselect ismaili ist istanbul it itau
itv iveco iwc jaguar java jcb jcp je jeep jetzt jewelry jio jlc jll jm jmp
jnj jo jobs joburg jot joy jp jpmorgan jprs juegos juniper kaufen kddi ke
kerryhotels kerrylogistics kerryproperties kfh kg kh ki kia kim kinder
kindle kitchen kiwi km kn koeln komatsu kosher kp kpmg kpn kr krd kred
kuokgroup kw ky kyoto kz la lacaixa ladbrokes lamborghini lamer lancaster
lancia lancome land landrover lanxess lasalle lat latino latrobe law lawyer
lb lc lds lease leclerc lefrak legal lego lexus lgbt li liaison lidl life
lifeinsurance lifestyle lighting like lilly limited limo lincoln linde link
lipsy live living lixil lk llc llp loan loans locker locus loft lol london
lotte lotto love lpl lplfinancial lr ls lt ltd ltda lu lundbeck lupin luxe
luxury lv ly ma macys madrid maif maison makeup man management mango map
market marketing markets marriott marshalls maserati mattel mba mc mcd
mcdonalds mckinsey md me med media meet melbourne meme memorial men menu meo
merckmsd metlife mf mg mh miami microsoft mil mini mint mit mitsubishi mk ml
mlb mls mm mma mn mo mobi mobile mobily moda moe moi mom monash money
monster montblanc mopar mormon mortgage moscow moto motorcycles mov movie
movistar mp mq mr ms msd mt mtn mtpc mtr mu museum mutual mutuelle mv mw mx
my mz na nab nadex nagoya name nationwide natura navy nba nc ne nec net
netbank netflix network neustar new newholland news next nextdirect nexus nf
nfl ng ngo nhk ni nico nike nikon ninja nissan nissay nl no nokia
northwesternmutual norton now nowruz nowtv np nr nra nrw ntt nu nyc nz obi
observer off office okinawa olayan olayangroup oldnavy ollo om omega one ong
onl online onyourside ooo open oracle orange org organic orientexpress
origins osaka otsuka ott ovh pa page pamperedchef panasonic panerai paris
pars partners parts party passagens pay pccw pe pet pf pfizer pg ph pharmacy
phd philips phone photo photography photos physio piaget pics pictet
pictures pid pin ping pink pioneer pizza pk pl place play playstation
plumbing plus pm pn pnc pohl poker politie porn post pr pramerica praxi
press prime pro prod productions prof progressive promo properties property
protection pru prudential ps pt pub pw pwc py qa qpon quebec quest qvc
racing radio raid re read realestate realtor realty recipes red redstone
redumbrella rehab reise reisen reit reliance ren rent rentals repair report
republican rest restaurant review reviews rexroth rich richardli ricoh
rightathome ril rio rip rmit ro rocher rocks rodeo rogers room rs rsvp ru
rugby ruhr run rw rwe ryukyu sa saarland safe safety sakura sale salon
samsclub samsung sandvik sandvikcoromant sanofi sap sapo sarl sas save saxo
sb sbi sbs sc sca scb schaeffler schmidt scholarships school schule schwarz
science scjohnson scor scot sd se search seat secure security seek select
sener services ses seven sew sex sexy sfr sg sh shangrila sharp shaw shell
shia shiksha shoes shop shopping shouji show showtime shriram si silk sina
singles site sj sk ski skin sky skype sl sling sm smart smile sn sncf so
soccer social softbank software sohu solar solutions song sony soy space
spiegel sport spot spreadbetting sr srl srt ss st stada staples star starhub
statebank statefarm statoil stc stcgroup stockholm storage store stream
studio study style su sucks supplies supply support surf surgery suzuki sv
swatch swiftcover swiss sx sy sydney symantec systems sz tab taipei talk
taobao target tatamotors tatar tattoo tax taxi tc tci td tdk team tech
technology tel telecity telefonica temasek tennis teva tf tg th thd theater
theatre tiaa tickets tienda tiffany tips tires tirol tj tjmaxx tjx tk tkmaxx
tl tm tmall tn to today tokyo tools top toray toshiba total tours town
toyota toys tp tr trade trading training travel travelchannel travelers
travelersinsurance trust trv tt tube tui tunes tushu tv tvs tw tz ua ubank
ubs uconnect ug uk um unicom university uno uol ups us uy uz va vacations
vana vanguard vc ve vegas ventures verisign versicherung vet vg vi viajes
video vig viking villas vin vip virgin visa vision vista vistaprint viva
vivo vlaanderen vn vodka volkswagen volvo vote voting voto voyage vu vuelos
wales walmart walter wang wanggou warman watch watches weather
weatherchannel webcam weber website wed wedding weibo weir wf whoswho wien
wiki williamhill win windows wine winners wme wolterskluwer woodside work
works world wow ws wtc wtf xbox xerox xfinity xihuan xin xperia xxx xyz
yachts yahoo yamaxun yandex ye yodobashi yoga yokohama you youtube yt yun za
zappos zara zero zip zippo zm zone zuerich zw
""".split())

#: A DNS label: alnum-bounded, hyphens allowed inside, 1–63 chars — the
#: shape ``twitter-text``'s own host regex requires per label.
_HOST_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"

# A candidate link token: optional ``http(s)://``, optional ``www.``, a
# dotted host of one-or-more labels ending in an alphabetic 2–63 char
# label (captured as ``tld``), an optional ``:port``, an optional
# ``/path?query#fragment`` run to the next whitespace. The leading
# negative lookbehind keeps this from matching mid-token (e.g. the
# second half of an ``user@host.tld`` email, which X does not auto-link).
_URL_CANDIDATE_RE = re.compile(
    r"(?<![\w@.-])"
    r"(?P<scheme>https?://)?"
    r"(?:www\.)?"
    r"(?P<host>" + _HOST_LABEL + r"(?:\." + _HOST_LABEL + r")*\.(?P<tld>[A-Za-z]{2,63}))"
    r"(?::\d+)?"
    r"(?:/[^\s]*)?",
    re.IGNORECASE,
)


def _url_spans(text: str) -> list[tuple[int, int]]:
    """Non-overlapping ``(start, end)`` spans X's parser would treat as a
    link: an explicit ``http(s)://`` always qualifies (no TLD check —
    matches ``twitter-text``, which trusts an explicit scheme); a bare
    dotted token qualifies only when its last label is a real TLD, so
    ``config.log`` stays plain text while ``x-browser.py`` does not.
    """
    spans = []
    for m in _URL_CANDIDATE_RE.finditer(text):
        if m.group("scheme") or m.group("tld").lower() in _VALID_TLDS:
            spans.append(m.span())
    return spans


def weighted_length(text: str) -> int:
    """The length X's 280-character limit is actually measured against —
    ``twitter-text`` weight, *not* ``len(text)``. Every link-shaped span
    (:func:`_url_spans`) is charged a flat :data:`TRANSFORMED_URL_LENGTH`
    in place of its own weight, however short the token; everything else
    is summed code point by code point via :func:`_char_weight`.
    """
    spans = _url_spans(text)
    total = 0
    i = 0
    si = 0
    n = len(text)
    while i < n:
        if si < len(spans) and spans[si][0] == i:
            total += TRANSFORMED_URL_LENGTH
            i = spans[si][1]
            si += 1
            continue
        total += _char_weight(ord(text[i]))
        i += 1
    return total


def link_charges(text: str) -> list[tuple[str, int]]:
    """``(token, cost)`` for every link-shaped span in *text* — the
    breakdown a refusal or ``--dry-run`` report names, so "over 280"
    comes with *why* instead of X's own fieldless 403.
    """
    return [(text[s:e], TRANSFORMED_URL_LENGTH) for s, e in _url_spans(text)]


def _refuse_if_overlength(text: str) -> None:
    """``SystemExit`` before any write call (dry-run included, so a
    preview is an honest preview) when *text* exceeds X's real limit —
    naming the weighted count, the raw ``len()``, and which tokens were
    charged as links and why, since the API's own 403 names none of it.
    """
    wlen = weighted_length(text)
    if wlen <= MAX_WEIGHTED_LENGTH:
        return
    charges = link_charges(text)
    if charges:
        charged = "; ".join(f"{tok!r} -> {cost} (parses as a link)" for tok, cost in charges)
    else:
        charged = "no link-shaped tokens — plain overlength text"
    raise SystemExit(
        f"refusing: weighted length {wlen} exceeds X's {MAX_WEIGHTED_LENGTH} "
        f"(len() == {len(text)}, which is not what X counts). "
        f"Link-shaped tokens charged flat {TRANSFORMED_URL_LENGTH} each: {charged}"
    )


# ── post / reply / delete: the CLI mechanics ─────────────────────────


def run_post(argv: list[str], paths: Paths) -> None:
    """The post/reply/delete mechanics — argv-compatible with the account
    shim's ``x-post.py``. Errors and usage raise ``SystemExit(message)``,
    exactly as the original script did; success paths ``print`` and
    return.

    ``-h``/``--help`` (and empty argv) resolve *before* anything else in
    this function reaches for the token, the env file, or the wire — the
    regression this module exists to pin.
    """
    args = list(argv)
    if not args or "-h" in args or "--help" in args:
        raise SystemExit(POST_USAGE.strip())
    if args[0] == "delete":
        if len(args) != 2:
            raise SystemExit("usage: envoy-x post delete <tweet-id>")
        out = delete(args[1], token(paths.env), paths)
        _append_receipt(paths.log, {
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "id": args[1], "action": "deleted",
        })
        print(json.dumps(out))
        return
    as_json = "--json" in args and (args.remove("--json") or True)
    dry = "--dry-run" in args and (args.remove("--dry-run") or True)
    reply_to = None
    if "--reply-to" in args:
        i = args.index("--reply-to")
        reply_to = args[i + 1]
        del args[i : i + 2]
    if len(args) != 1 or not args[0].strip():
        raise SystemExit(
            POST_USAGE.strip().split("\n", 1)[0] + "\n(one non-empty text argument required)"
        )
    text = args[0]
    if text.startswith(" -"):
        # The escape hatch: a single leading space marks the dash that
        # follows as deliberate text, not a flag — consumed here, so it
        # never reaches the wire as part of the post.
        text = text[1:]
    elif text.startswith("-"):
        raise SystemExit(
            "refusing: text starts with '-' — looks like a flag, not a post. "
            "Quote deliberately dash-led text as ' -…' with a leading space."
        )

    payload: dict[str, Any] = {"text": text}
    if reply_to:
        payload["reply"] = {"in_reply_to_tweet_id": str(reply_to)}

    # Before any write call — dry-run included, so a preview is an honest
    # preview instead of the thing that used to pass here and get refused
    # live with a fieldless 403.
    _refuse_if_overlength(text)

    if dry:
        if as_json:
            print(json.dumps({"would_post": payload}))
        else:
            wlen = weighted_length(text)
            line = f"dry-run · {wlen} chars"
            if wlen != len(text):
                charges = link_charges(text)
                charged = ", ".join(f"{tok!r}->{cost}" for tok, cost in charges)
                line += f" (len() == {len(text)}, not what X counts; link-charged: {charged})"
            print(line + (f" · reply-to {reply_to}" if reply_to else "") + f"\n{text}")
        return

    out = post(payload, token(paths.env), paths)
    tweet_id = (out.get("data") or {}).get("id")
    _append_receipt(paths.log, {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "id": tweet_id, "reply_to": reply_to, "text": text,
    })
    if as_json:
        print(json.dumps(out))
    else:
        print(f"posted · https://x.com/i/status/{tweet_id}"
              + (f" · reply-to {reply_to}" if reply_to else ""))


def main_post(argv: list[str], home_dir: Path | str) -> None:
    """Convenience wrapper: :func:`run_post` over ``Paths.in_dir(home_dir)``."""
    run_post(argv, Paths.in_dir(home_dir))


# ── read: mentions + own-tweet metrics ───────────────────────────────


def run_read(argv: list[str], paths: Paths) -> None:
    """The read mechanics — argv-compatible with the account shim's
    ``x-read.py``. On-demand only: this never wakes anything, it is a
    door peeked through when a run reaches for it.
    """
    show_all = "--all" in argv
    as_json = "--json" in argv
    tok = token(paths.env)

    me, tok = get("/users/me", {"user.fields": "public_metrics"}, tok, paths)
    uid = me["data"]["id"]
    pm = me["data"].get("public_metrics", {})

    state: dict[str, Any] = {}
    if paths.state.exists():
        state = json.loads(paths.state.read_text(encoding="utf-8"))

    params = {
        "tweet.fields": "created_at,author_id",
        "expansions": "author_id",
        "user.fields": "username",
        "max_results": 25,
    }
    if state.get("since_id") and not show_all:
        params["since_id"] = state["since_id"]
    mentions, tok = get(f"/users/{uid}/mentions", params, tok, paths)

    tweets, tok = get(
        f"/users/{uid}/tweets",
        {"tweet.fields": "public_metrics,created_at", "max_results": 5},
        tok, paths,
    )

    rows = mentions.get("data") or []
    users = {u["id"]: u["username"] for u in mentions.get("includes", {}).get("users", [])}
    if rows:
        newest = max(int(r["id"]) for r in rows)
        if newest > int(state.get("since_id") or 0):
            paths.state.write_text(json.dumps({"since_id": str(newest)}), encoding="utf-8")

    if as_json:
        print(json.dumps({"metrics": pm, "mentions": rows, "own_recent": tweets.get("data") or []}))
        return

    print(f"@{me['data']['username']} · followers {pm.get('followers_count')} · "
          f"following {pm.get('following_count')} · posts {pm.get('tweet_count')} · "
          f"listed {pm.get('listed_count')}")
    if not rows:
        print("mentions: none new since last look" + (" (use --all for history)" if not show_all else ""))
    for r in rows:
        who = users.get(r["author_id"], r["author_id"])
        text = r["text"].replace("\n", " ")
        print(f"@{who} · {r.get('created_at', '?')} · {text[:200]} · "
              f"https://x.com/{who}/status/{r['id']}")
    for t in tweets.get("data") or []:
        m = t.get("public_metrics", {})
        text = t["text"].replace("\n", " ")
        print(f"own · {text[:60]!r} · ❤ {m.get('like_count', 0)} · rt {m.get('retweet_count', 0)} · "
              f"replies {m.get('reply_count', 0)} · views {m.get('impression_count', 0)}")


def main_read(argv: list[str], home_dir: Path | str) -> None:
    """Convenience wrapper: :func:`run_read` over ``Paths.in_dir(home_dir)``."""
    run_read(argv, Paths.in_dir(home_dir))
