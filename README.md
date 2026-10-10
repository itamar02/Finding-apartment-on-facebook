# ApartmentBot

Finds apartment listings in the Facebook groups you choose and filters them against your
own, editable requirements. Collecting posts is fully deterministic (no AI, no cost);
understanding each listing uses Claude (Haiku 4.5 by default), with the simple absolute
rules double-checked in plain code. Built for Hebrew rental groups in Israel.

```
RAW FACEBOOK LISTING          scan_facebook.py  -> posts table (never modified)
→ AI EXTRACTION + FILTER      analyze_posts.py  -> post_analyses table
→ HARD-RULE VERIFICATION      (same run, plain Python)
→ LOCATION / GEOCODING        not built yet
→ COMMUTE DATA                not built yet
→ MARKET COMPARISON           not built yet
→ FINAL AI EVALUATION         not built yet
```

> **Read before using**
> - **Facebook's terms:** automated collection of Facebook content is against Facebook's
>   terms of service. This tool drives a real browser logged in to *your own* account at a
>   human-like pace, reads only, and never likes, comments, messages, posts or joins - but
>   you use it at your own risk (including the risk of account restrictions).
> - **Privacy:** the database holds other people's names, profile links and phone numbers.
>   Keep it private. `.gitignore` keeps the database, photos and exported results out of git;
>   do not publish them.
> - **Cost:** the AI step uses the paid Anthropic API, roughly $0.001-0.006 per post (see
>   *Costs*). Scanning is free.
> - **Platform:** developed and tested on Windows 11 with Python 3.11 and Google Chrome.

## Quick start

1. **Install the prerequisites**: Python 3.11+, Google Chrome, and [uv](https://docs.astral.sh/uv/).
2. **Install the Python packages**:
   ```
   pip install -r requirements.txt
   ```
3. **Install browser-harness**, the tool that drives Chrome:
   ```
   uv tool install --python 3.12 --upgrade --force browser-harness
   ```
   On Windows with **Smart App Control** on, the Python that uv downloads can be blocked
   (see *Troubleshooting*). Then install the Microsoft Store Python 3.12 once
   (`winget install 9NCVDN91XZQP`) and use it instead:
   ```
   uv tool install --python "%LOCALAPPDATA%\Microsoft\WindowsApps\python3.12.exe" --upgrade --force browser-harness
   ```
4. **Create your configuration**: copy `config.example.yaml` to `config.yaml` and edit it -
   your profile (age, gender), budget, allowed listing types, areas, and the Facebook
   groups to scan. `config.yaml` is private and git-ignored. See *Configuration*.
5. **Set your Anthropic API key** (only the AI step needs it). Either set an environment
   variable (default name `ANTHROPIC_API_KEY`), or put the key in `config.yaml` under
   `app.api_key`. See *API key*.
6. **Log in to Facebook in the bot's own Chrome, once**:
   ```
   python bot_chrome.py
   ```
   A separate Chrome window opens (see *The bot's Chrome*). Log in to Facebook there.
7. **Run it**:
   ```
   python scan_facebook.py            # collect posts            (free)
   python analyze_posts.py --limit 20 # try the AI on 20 posts   (a few cents)
   python analyze_posts.py --all      # analyze everything pending
   python export_results.py results.txt
   ```

Normal routine afterwards: scan, analyze, export.

## The bot's Chrome

`bot_chrome.py` starts a **separate Chrome profile just for the bot** (there is no `.bat`
file; it is this Python script - run it from a terminal, or put a shortcut to
`python bot_chrome.py` wherever you like). Why it exists: Chrome pauses pages in
minimized or covered windows, and those settings can only be switched off when Chrome
starts. The bot Chrome is launched with them off and with remote debugging on its own
local port, so:

- it keeps scanning while minimized, behind other windows, or while you work;
- your normal Chrome, tabs and logins are never touched;
- there are no "Allow remote debugging?" prompts;
- its logins live in `browser.profile_dir` (default `%LOCALAPPDATA%\ApartmentBot\chrome-profile`),
  outside the project, so they can never end up in git.

`python bot_chrome.py` starts it visibly (to log in) or brings an already-running window
back on screen. `scan_facebook.py` starts it automatically when it isn't running and
parks its window off-screen. Settings: `browser` in `config.yaml` (`chrome_path`,
`profile_dir`, `debug_port`).

## Usage

### 1. Scan the Facebook groups (no AI, no cost)

```
python scan_facebook.py                          # all groups selected in the app
python scan_facebook.py --group 123456789012345  # only some groups (repeatable)
python scan_facebook.py --max-age-days 3         # only the last 3 days this time
python scan_facebook.py --full                   # re-read every group back to the age limit
```

Prints one line per group and a summary, e.g.
`Scanned 312 posts in 25 groups, found 140 new posts.`

- Runs in the bot Chrome, using one tab for all groups, each sorted by **New posts**. The
  sorting is just a URL parameter on that tab (`?sorting_setting=CHRONOLOGICAL`); it
  doesn't change any Facebook setting.
- Stores **every** post in each group, listing or not. The scanner does no filtering;
  filtering happens in `analyze_posts.py`.
- Reads posts one by one, clicking only "See more" inside post bodies. It never likes,
  comments, messages, posts or joins anything.
- **Reads every recent post.** "Recent" is `listing.max_post_age_days` in `config.yaml`
  (the largest value across users; override with `--max-age-days`). Each group is read
  newest-first until 3 consecutive posts are older than that.
- After a run finishes normally, later runs stop early when they reach 3 consecutive
  already-stored posts. If a run ended with an error, the next run ignores that shortcut
  and reads back to the age limit again, so no gap is left. `--full` forces that for all
  groups. Very busy groups stop at the safety cap `--max-posts` (default 300); later runs
  continue from the newest posts.
- Only one scan can run at a time (a second one stops with a message).
- The safety check "navigation away from the group detected" stops a group if its page
  changes during the scan - e.g. something else used the bot Chrome at the same time.
- A group that fails (e.g. you are not a member) is logged in `scan_runs` and skipped; the
  other groups still run.
- Takes roughly 2-3 seconds per post read. The first scan of many busy groups can take
  hours; later scans are much shorter. Nothing is printed for a group until it finishes.

### 2. Analyze new posts with AI

```
python analyze_posts.py --limit 5                  # up to 5 not-yet-analyzed posts
python analyze_posts.py --all                      # every pending post
python analyze_posts.py --post-id 123 --force      # re-analyze a specific post
python analyze_posts.py --limit 5 --report out.json  # also write a report of this run
python analyze_posts.py --all --user friend1       # when config has several users
```

A post is analyzed once per user and pipeline version. Re-runs skip it unless you pass
`--force` or the prompt/schema version changes (see *Extending*). Each post is saved
the moment it is analyzed, so you can stop with Ctrl+C and run it again later; it continues
where it stopped. It prints the cost of the run and the total spent so far.

### Photos

Photos are kept only for listings that pass the filter. The scanner records the links
of the photos shown in the feed (up to ~5 per post; more hidden behind a "+N" tile are
counted, not fetched). After analysis, `analyze_posts.py` downloads the files for posts
whose final decision is in `pictures.save_for` (default: `PASS`) into

```
pictures/<post date>_<post id>/01.jpg, 02.jpg, ...
```

Facebook's photo links expire after some days, so analyze soon after scanning. To retry
failed downloads, or after adding `NEEDS_REVIEW` to `save_for`:

```
python download_pictures.py
python download_pictures.py --post-id 123 --ignore-decision   # force one listing
```

Photos are feed-size (around 600 px wide): good for a first look, not full resolution.

### Export results to a file (read-only, no cost)

```
python export_results.py results.txt    # readable report
python export_results.py results.json   # same content as JSON
python export_results.py results.txt --all-dates   # include listings older than the recent window
```

Only recent listings are exported by default (posted within the recent window, see *Phone
app*); `--all-dates` exports everything.

Order: **PASSED**, then **NEEDS REVIEW**, then a `-----` line, then **HUMAN REJECTED**
(listings you rejected by hand) and **REJECTED** (automatic; shown compactly). Each listing has the decision and reason, the link, the poster's name and
Facebook profile, phone numbers and WhatsApp links found in the text, a "best contact"
line (phone, else WhatsApp, else Facebook profile), key facts, photos, and the full post
text. The JSON version has `passed` / `needs_review` / `rejected` sections instead of the
separator line. It uses each post's latest analysis. Every export rewrites the file with
everything stored so far.

`analyze_posts.py --report FILE` writes the same format, but only for the posts analyzed
in that run.

### Find Facebook groups

```
python discover_groups.py                        # your groups + the searches set in the app
python discover_groups.py --query "שותפים חיפה"   # only this search (repeatable)
python discover_groups.py --score                # also rate each group 0-10 with the AI (cheap)
```

Reads, in the bot Chrome, the list of groups you are in and Facebook's group search results
(name, members, public/private, whether you're a member). Read-only, at a human pace, and
never at the same time as a scan (same lock). New groups are added unselected; pick them in
the app. The default searches are "דירות להשכרה <city>" and "שותפים <city>" for each
`listing.search_cities` entry; edit them in the app's Groups screen.

### Reject a listing by hand

Looked at a listing and don't want to see it again? Take its post link (the `Link:` line in
an exported report) or post ID:

```
python reject.py https://www.facebook.com/groups/123456789012345/posts/3289385131257313/
python reject.py 3289385131257313 3289385131257999 --note "already taken"
python reject.py --list                  # everything you rejected so far
python reject.py --undo 3289385131257313 # take a rejection back
```

Rejected listings move to the **HUMAN REJECTED** section of every export (below the `-----`
line), are skipped by the AI (no cost) and stay rejected when the analysis is re-run. It
works for posts that were never analyzed too. Nothing is deleted: the post, its analysis
and its photos stay where they are, and `--undo` returns the listing to its automatic
decision. The decisions are stored in the `human_decisions` table, separate from the AI's.

### 3. Ask why a listing was rejected

```
python why.py 3289385131257313
python why.py https://www.facebook.com/groups/123456789012345/posts/3289385131257313/
python why.py 3289385131257313 --json      # everything stored, incl. raw AI output
```

```
Post 3289385131257313: REJECT
  Rejected because the rent (no extra monthly costs stated) is 8,700 ILS and the configured absolute maximum is 5,000 ILS.
  rule: rent_absolute_max   decided by: ai+hard_rule
```

Hebrew is never printed raw to the terminal (Windows consoles reverse it); it shows as
`[Hebrew]`. The original text is stored unchanged - use `--json`, an export file, or a
SQLite viewer to read it.

## Phone app

A phone app (installable web app, Hebrew, works in light and dark mode) shows the listings as
a feed and a board, keeps **your own status** for each listing (new, interested, contacted,
meeting scheduled, ...) next to the AI's verdict, and opens WhatsApp with a ready opening
message. The PC is the server: when it is off, the app doesn't work. Nothing is copied to a
cloud.

### One-time setup

Short version (Windows 11, Python 3.11+, Node.js 20+):

```
python setup_app.py        # packages + builds the web app + checks Tailscale
```

then the four commands it prints. In detail:

1. **Packages and the web app**: `python setup_app.py` (does `pip install -r requirements.txt`
   and `npm ci && npm run build` in `web/`, and copies `config.example.yaml` to `config.yaml`
   if you have none - edit it, see *Configuration*). Run it again after pulling app changes.
2. **Tailscale** (private network between your PC and phone, with HTTPS - needed to install
   the app and for notifications): install Tailscale on the PC (`winget install
   Tailscale.Tailscale`) and on the phone and log in to the same account on both. In the
   Tailscale admin console, under DNS, turn on **MagicDNS** and **HTTPS certificates**. Then,
   on the PC, once:
   ```
   tailscale up
   tailscale serve --bg 8787
   ```
   `serve` prints the app's address, like `https://my-pc.tail1234.ts.net`. It only works on
   your own devices; the server itself listens only on the PC (`127.0.0.1`).
3. **Start the server**: `python app_server.py` (or double-click `run_app.cmd`). The first
   start upgrades the database (with a backup, as always). On a brand new install with no
   database yet this just creates an empty one; run `python scan_facebook.py` to fill it.
4. **Log in from the phone**: open the address from step 2 on the phone. On the PC run
   ```
   python app_server.py --pair
   ```
   and type the 8-digit code into the app (valid 10 minutes, one use). The phone stays logged
   in. `python app_server.py --reset-token` logs out every phone.
5. **Install the app**: Android Chrome: menu > *Add to home screen* / *Install app*. iPhone
   Safari: Share > *Add to Home Screen*.

### Android app (APK)

If you prefer an app you install from a file over adding the web page to the home screen,
`build_apk.py` builds a small Android shell (Capacitor, in `android-app/`) that opens the same
app on your PC. Nothing is stored in the APK, so you only rebuild it when `android-app/`
changes. Everything above (Tailscale, the server, pairing) is still needed.

Needs on the PC: Node.js 20+, a JDK 17-21 (Android Studio's bundled one is found
automatically; otherwise `winget install Microsoft.OpenJDK.21`) and the Android SDK with
build-tools and a platform and its licenses accepted (installing Android Studio does this;
set `ANDROID_HOME` if it is somewhere unusual). Then:

```
python build_apk.py --url https://<your-pc>.<tailnet>.ts.net
```

(the address `tailscale serve` printed; leave `--url` out and the app asks for it on first
start). The first build downloads Gradle and takes a few minutes. The result is
`dist/ApartmentBot.apk` (git-ignored), a debug build for your own phone: copy it to the phone,
open it, and allow installs from that source when Android asks. Log in with
`python app_server.py --pair` as for the web page.

Limits of the APK version: the Web Push notifications don't work inside it (use the Telegram
channel, or the web-page version for push); sharing a WhatsApp chat *to* the app isn't set up
(paste it or upload the exported `.txt`); the PC and the phone's Tailscale app must be on.

### Start it automatically with Windows

```
powershell -ExecutionPolicy Bypass -File register_autostart.ps1
```

creates a Task Scheduler task that starts `app_server.py` when you log in (`-Remove` deletes
it). `tailscale serve --bg` survives restarts by itself.

### What's in it

- **Feed**: one card per listing with the AI verdict and reason, rent / area / rooms / type /
  move-in, photos, the text, the group, the post's age and a link to the Facebook post.
  Filters: AI decision, my status, who set it (me / GPT / system), group, price, area, date,
  has phone. Sort by newest or price. Listings older than the recent window (default: the
  largest `listing.max_post_age_days`; changeable in Settings) are hidden unless you ask.
- **WhatsApp button** when the post has a mobile number or a WhatsApp link: opens a chat with a
  prefilled message from the template in Settings (placeholders `{name}` `{area}` `{street}`
  `{group}` `{price}`; a part in `[square brackets]` is left out when its value is unknown).
  Tapping it marks the listing *Contacted*, with Undo. Without a phone it opens the poster's
  Facebook profile. Nothing is ever sent automatically.
- **My status**: a pipeline separate from the AI verdict. The list (names, colors, order, which
  ones ask for a note such as *Needs details*) is editable in Settings. Every change is kept in
  a history. *Not relevant* is the same as `reject.py`: setting either one sets both.
- **Overruling the AI**: the AI's verdict (passed / needs review / rejected) is never changed
  - it stays on the card, dimmed as "AI: ..." once you have decided. Your decision is your
  own status: 👍 on a card sets *Interested*, 👎 sets *Not relevant* (tap again to undo; the
  status picker has the rest). Listings marked *Not relevant* or *Taken* are hidden from the
  feed unless you tick "show those too" in the filter, so to rescue something the AI rejected,
  filter on *Rejected* and tap 👍. Your decisions also feed the suggestions (see below).
- **Board**: columns by status. **Notes**: free text on any listing.
- **Groups**: tick the Facebook groups to scan. The list lives in the database now;
  `facebook.groups` in `config.yaml` only fills it the first time. *Find groups* runs
  `discover_groups.py` on the PC (see below). Groups you are not a member of show a link to
  join on Facebook; joining stays manual.
- **Sorting with ChatGPT or another chat AI** (Settings): *Export* gives the listings without
  a status set by you (text, facts, AI verdict, contacts) plus a ready prompt; paste it into
  the chat, paste the answer back, check the preview, and confirm. Imported statuses are
  marked GPT (the feed can filter by who set a status) and never replace a status you set
  yourself unless you tick that box. A GPT "not relevant" is not a rejection by you, so
  `reject.py --list` and the exports' HUMAN REJECTED section only show your own. The export
  holds other people's phone numbers - keep that in mind before pasting it into a service.

### Automatic scanning, notifications and reminders

In **Settings**:

- **Automatic scanning**: the server runs `scan_facebook.py`, then `analyze_posts.py --all`,
  then sends notifications, every N minutes (default 30), except during its quiet hours. It
  is off until you turn it on; *Scan now* runs it once. This replaces the watchdog - don't
  run both, they would take turns waiting for the scan lock.
- **Notifications** go to the installed app (tap *Turn on on this phone*; on an iPhone this
  only works from the home-screen app, iOS 16.4+) and/or a Telegram bot (token from
  BotFather + your chat id; if the watchdog's bot is set up it is used automatically). You
  choose which AI decisions are announced, a price ceiling, quiet hours (notifications wait
  and are sent when they end), and whether to be told about:
  - **edited posts**: when a post you have already seen (opened in the app, or given a
    status there or through a GPT import) is edited, with a short "added / removed" note.
    The scanner keeps the first text in `posts` and stores every later edit in
    `post_versions`; the app shows the current text and the earlier versions, and an edited
    post is analyzed again. The scanner only re-reads posts near the top of each group, so an
    edit to an old post can go unnoticed.
  - **follow-ups**: a listing that stays in *Contacted*, *Awaiting reply* or *Needs details*
    longer than the hours you set per status. They are also listed at the top of the feed
    with *Snooze a day* and *Dismiss*.
  - **price drops**: the same apartment posted again for less (see below).
- **Price tracking**: posts are grouped into one apartment when the text is identical, or
  when they share a phone number or an author and the text is at least 80% similar. A card
  shows the earlier prices and highlights a drop.

### Visits and scheduling viewings

- **Visit checklist**: a listing at *Meeting scheduled*, *Visited* or *Top candidate* gets a
  checklist in its details (water pressure, mold, noise, storage, parking, bills, landlord -
  the list is editable in Settings): OK / problem / not relevant per item, notes, a 1-5
  rating, and photos you take with the phone. The photos are stored only on the PC, in
  `uploads/visits/` (git-ignored). The **Visits** tab compares the apartments side by side.
- **From a WhatsApp chat to the calendar**: share the chat to the app (Android: in WhatsApp,
  *More > Export chat > Without media*, then pick the app; this works once the app is
  installed), or paste it, or upload the exported `.txt` (the way to do it on an iPhone). The
  app links the chat to a listing by a phone number that appears in both, or asks you to pick
  one. The AI (stage *meeting* in the AI providers settings) proposes a date, time and address
  **only if the chat shows that you explicitly agreed to a time**, and quotes the lines that
  show it; the code checks every quote is really in the chat. Nothing is added to your
  calendar until you check the details and tap **Confirm**: then one Google Calendar event is
  created (address, phone, link to the post and your notes) and the status becomes *Meeting
  scheduled*. The app never connects to WhatsApp itself and uses no unofficial WhatsApp
  library. Set *My name in WhatsApp* in Settings so the AI can tell your messages apart.

### Google Calendar

Once, on the PC:

1. In the [Google Cloud Console](https://console.cloud.google.com/): create a project, enable
   the **Google Calendar API**, set up the OAuth consent screen (type *External*, add yourself
   as a test user), and create an **OAuth client ID** of type **Desktop app**. Download its
   JSON and save it as `secrets/google_client_secret.json`.
2. Run `python app_server.py --google-login`. A browser opens on the PC; allow access. The
   token is saved in `secrets/google_token.json` (git-ignored). Only permission to add and
   edit calendar events is requested.

### My requirements and suggestions from my own choices

- **My requirements** (Settings, first section): age, budget, listing types, cities and areas,
  broker policy, roommates, required / nice-to-have / dealbreaker features, move-in dates and
  free text - the same settings as `config.yaml`, edited from the phone. Saving writes them
  back into `config.yaml` with ruamel.yaml, so its comments and order stay; the old file is
  kept as `config.backup-<time>.yaml` (git-ignored), and the new one is checked with the
  normal config validation before it replaces the old one. A changed config changes the
  `config_hash` stored with new analyses, so `why.py` can tell a decision was made with older
  settings; existing analyses are not redone automatically.
- **Suggestions** (Settings, second section; off / weekly / on demand): the AI (stage
  *learning*) reads the listings you marked *Not relevant* (especially with a note), the ones
  you liked, and problems from your visits, and suggests small concrete changes, for example
  "you rejected 8 ground-floor apartments; add 'ground floor' to dealbreakers?". Only changes
  to the fields above, of the right type, backed by at least 3 of your listings are kept; a
  suggestion you declined is not suggested again. Nothing changes until you tap *Accept*.

### Free or cheaper AI providers

Settings > *AI providers* lets each analysis stage (gate, quick pass, full analysis, group
rating) use any OpenAI-compatible API instead of Anthropic: Groq, OpenRouter, Gemini's
OpenAI-compatible endpoint, or a local server. Presets fill in the address and a model; the
API key is stored only in `secrets/ai_keys.json` (never in the database, which gets copied
and sent). Per-minute and per-day limits queue requests or stop using that provider for the
day. Every answer is checked against the same JSON schema as the Anthropic one; an invalid
answer is retried once, and then - like a provider that fails or hits its daily limit - the
request falls back to Anthropic. Free tiers may use what you send them for training, and the
posts contain other people's names and phone numbers. Models and free limits change often;
check each provider's page.

### Developing the app

`python app_server.py --insecure-cookie` in one terminal and `cd web && npm run dev` in
another; open http://localhost:5173. Tests: `python -m pytest`.

## Configuration

All user-specific values live in `config.yaml`, created by copying the committed template
`config.example.yaml`; nothing personal is in the Python code. Each entry under `users` is
an independent profile, so friends can be added as extra entries with their own `id`.
`facebook.groups` (shared by all users) lists the groups to scan, each as
`{id: "...", name: "..."}` where `id` is the number or name in the group's URL
(`facebook.com/groups/<id>/`). You must be a member of private groups.

| Section | What it controls |
|---|---|
| `app` | `ai_model`, `api_key` (see below), `stop_at_first_dealbreaker` (cost saving, see *How a decision is made*) |
| `browser` | Chrome path, the bot profile folder and debug port (see *The bot's Chrome*) |
| `pictures` | photo folder and which decisions get their photos downloaded |
| `facebook.groups` | the groups to scan |
| `profile` | `age`, `gender` (male/female) - used for roommate eligibility |
| `listing` | `max_post_age_days`, `allowed_types` (`whole_apartment`, `roommate_search`, `room_replacement`, `sublet`), `search_cities` |
| `budget` | `preferred_max_rent` (soft), `absolute_max_rent` (hard), `absolute_max_applies_to` (`rent` or `rent_plus_known_costs`) |
| `areas` | `allowed` (empty = anywhere in the search cities), `preferred` / `less_preferred` (soft plus / minus), `excluded` (rejected) |
| `broker` | `policy`: `allow`, `avoid` (soft negative), `reject` |
| `roommates` | preferred age range, `max_roommates`, `gender_preference_mismatch` (`needs_review` / `reject` / `ignore`) |
| `features` | `required`, `nice_to_have`, `dealbreakers` (free text, interpreted by the AI) |
| `move_in` | `earliest` / `latest` ISO dates or null |
| `commute` | destination and time limits - stored for a later commute stage; not used for filtering yet |
| `free_text` | anything else, in plain language |

The file is validated on every run; a mistake gives a readable `config error: ...`.
Missing information in a post never rejects it: a required feature only rejects when the
post says it is missing, and what a post leaves out is listed under "missing information".

### API key

```yaml
app:
  api_key:
    source: env                  # env | config
    env_var: ANTHROPIC_API_KEY   # source: env    -> the variable to read (any name)
    value: ""                    # source: config -> the key itself, e.g. "sk-ant-..."
```

- **`source: env`** (default): the key is read from the environment variable named in
  `env_var`. This keeps the key out of every file.
- **`source: config`**: the key is read from `value`. `config.yaml` is git-ignored, but
  anyone who can read the file can read the key - never share or commit it.

Only `analyze_posts.py` needs the key; scanning and exporting work without one. A missing
key gives a plain message saying which variable or setting to fix.

## How a decision is made

0. **Code rules before any AI call** (free): a post that duplicates an earlier, still-fresh
   post (same text, e.g. the same ad in another group) or is older than
   `max_post_age_days` is rejected right away, with no AI call. A repost whose first copy
   is already too old is *not* a duplicate - it is the live ad. These rules are also
   re-applied to **saved** results before every export and at the end of every analysis
   (`recheck_stored`): a listing analyzed earlier whose twin was scanned later, or that has
   since become too old, gets a new REJECT row (no AI, no cost), so the same ad is not
   shown twice.
1. **Gate** (when `app.stop_at_first_dealbreaker: true`, the default): the cheapest AI
   question - only "is this post offering a place to rent?" and the listing type, plus a
   confidence. The *code* then applies the configured rules to that answer (not a rental
   offer; listing type not in `allowed_types`). A confident rule violation ends the
   analysis (about $0.001 per post, with the code's own reason text). A low-confidence
   answer never rejects; it moves on.
2. **Quick first pass** (same setting): a short AI request returns only the decision plus
   the few facts the code checks need. If the rejection is confirmed by the code checks,
   the analysis ends there (about $0.002 per post). Otherwise - PASS, NEEDS_REVIEW, or a
   rejection the code could not confirm - the post continues to the full analysis.
   Posts rejected at the gate or in the quick pass carry only partial facts (`_partial`
   in the stored facts). Set the setting to `false` to always run the full analysis.
3. **AI stage** (`apartment_bot/ai_filter.py`): one request per post with the raw text,
   post date, today's date and the user's config. The response is forced into a JSON
   schema (structured outputs) containing:
   - `facts` - rent, extra costs, listing type, location, rooms, roommate gender/age
     requirements and whether they are strict or a preference, broker, move-in, etc.;
   - `rule_checks` - one entry per relevant rule with a verbatim quote as evidence;
   - `decision` - `REJECT` / `PASS` / `NEEDS_REVIEW`, the rule responsible, and a
     one-sentence reason.

   REJECT requires explicit evidence of a hard-requirement violation; ambiguity gives
   NEEDS_REVIEW; missing information alone never rejects. Price judgment and commute
   estimates are deliberately out of scope.
4. **Code stage** (`apartment_bot/hard_rules.py`): plain Python re-checks the absolute
   rules from the AI's facts - duplicate text, post age, not a rental offer, listing
   type, strict gender, strict age, rent above the absolute maximum. Monthly totals are
   computed here, not by the AI.
5. **Combination**: any failed code check forces REJECT (`decided_by` = `hard_rule`, or
   `ai+hard_rule` when both agree). If the AI rejects on gender/age but its quoted
   evidence is not actually in the post, the result becomes NEEDS_REVIEW. Otherwise the
   AI decision stands (`decided_by` = `ai`).

## Database (`apartments.db`, SQLite, created on first run)

| Table | Written by | Contents |
|---|---|---|
| `posts` | `scan_facebook.py` | Raw listings, never modified: `post_id` (Facebook ID, primary key), `group_id`, `author`, `author_id`, `posted_at` (ISO with offset), `posted_at_raw`, `text`, `text_truncated`, `url`, `first_seen_at` |
| `scan_runs` | `scan_facebook.py` | One row per group per scan: counts, stop reason, sort label seen, error |
| `post_images` | `scan_facebook.py`, `apartment_bot/pictures.py` | Photo links per post (`post_id`, `idx`, `url`, `alt`, `more_hidden`) and, once downloaded, `local_path` / `downloaded_at` / `download_error` |
| `human_decisions` | `reject.py` | Listings you rejected by hand: `post_id`, `user_id`, `decision`, `note`, `decided_at` |
| `fb_groups` | the app, `discover_groups.py` | The groups to scan (`selected`) and groups found: name, members, privacy, membership, optional AI relevance |
| `gpt_imports` | the app | Every status import from an external AI and what it changed |
| `post_versions` | `scan_facebook.py` | Later texts of edited posts (`posts` keeps the first one) |
| `listing_posts` | the app | Posts grouped into one apartment, with each post's rent (price history) |
| `notifications`, `followups`, `push_subscriptions`, `ai_calls` | the app | Sent / waiting notifications, snoozed or dismissed reminders, phones with notifications on, AI calls for rate limits |
| `visit_items`, `visits`, `visit_checks`, `visit_photos` | the app | The checklist, and per listing: rating, notes, answers, your photos (files in `uploads/visits/`) |
| `chats`, `meeting_proposals` | the app | WhatsApp chats you shared, and the meetings proposed from them (confirmed ones have the calendar event id) |
| `config_proposals` | the app | Suggested `config.yaml` changes, their evidence and your decision |
| `statuses`, `post_status`, `post_status_history`, `post_notes`, `post_seen`, `app_settings` | the phone app, `reject.py` | Your own status per listing (current + append-only history, source `human` / `gpt` / `system`), the editable status list, notes, which listings you opened, app settings |
| `post_analyses` | `analyze_posts.py` | One row per analysis attempt: AI decision/rule/reason, final decision/rule/reason, `decided_by`, `facts_json`, `hard_rules_json`, `raw_response_json`, `model`, `pipeline_version`, `config_hash`, token counts, `cost_usd`, `analyzed_at`, `status` (`ok`/`error`), `error` |

- Analyses never overwrite each other; the latest `ok` row per post and user is current.
  `status` only says whether the AI call itself worked; the decision is `final_decision`.
- `config_hash` records which config version a decision was made with; `why.py` warns
  when the config has changed since.
- Schema changes go through `apartment_bot/db.py` (`PRAGMA user_version`). Each
  migration backs the file up first as `apartments.backup-v<N>-<timestamp>.db`.

Useful queries:

```sql
-- current decision per post
SELECT post_id, final_decision, final_rule, final_reason
FROM post_analyses a
WHERE status = 'ok' AND id = (SELECT max(id) FROM post_analyses b
                              WHERE b.post_id = a.post_id AND b.user_id = a.user_id AND b.status = 'ok');

-- total AI spend
SELECT round(sum(cost_usd), 4) FROM post_analyses;
```

## Costs

Claude Haiku 4.5: $1 / $5 per million input / output tokens; cache writes $1.25, cache
reads $0.10. The instructions plus the user config (~4,500 tokens) are prompt-cached
for 5 minutes, so consecutive posts in a run pay mostly for output. Measured:

| Post | Cost |
|---|---|
| Rejected by a code rule (duplicate / too old) | $0 |
| Rejected at the gate (`stop_at_first_dealbreaker: true`) | about $0.001 |
| Rejected in the quick pass | about $0.002 |
| Full analysis (surviving posts, or the setting off) | about $0.006 |

The first AI call of a run costs more (about $0.007) because it writes the cache. Haiku
only caches prompts of at least 4,096 tokens; the gate and quick-pass prompts include
extra guidance partly to reach that size - without the cache they would cost two to
three times as much. `analyze_posts.py` prints the cost of every run and the total spent.

## Project layout

```
scan_facebook.py        scanner entry point: SQLite + runs the browser script
fb_harness_scan.py      browser script executed inside browser-harness (not plain Python)
analyze_posts.py        AI analysis entry point
export_results.py       write all stored results to a text or JSON report
app_server.py           the phone app's server (API + built web app)
setup_app.py            one-command setup of the phone app (packages, web build, checks)
run_app.cmd             start the server by double-click; register_autostart.ps1 starts it at login
build_apk.py            build the Android app (dist/ApartmentBot.apk) from android-app/
discover_groups.py      find groups (yours + search results); fb_harness_discover.py runs in the browser
why.py                  explain a stored decision
reject.py               reject (or un-reject) listings by hand - the "human rejected" pile
bot_chrome.py           start / show the bot's own Chrome (log in to Facebook there once)
download_pictures.py    download / retry photos of listings that passed
fb_time.py              Facebook timestamp parsing (shared by scanner and browser script)
config.example.yaml     configuration template (committed) - copy to config.yaml
config.yaml             YOUR settings and optionally API key (git-ignored)
requirements.txt        Python packages
apartment_bot/
  config.py             load + validate config.yaml, API key lookup
  db.py                 connection and schema migrations
  ai_filter.py          prompts, JSON schemas, Anthropic calls, cost calculation
  hard_rules.py         deterministic rule checks and decision combination
  pipeline.py           analyze one post end to end and store the result
  pictures.py           download photos for posts that passed
  human.py              the human's decisions (rejections by hand)
  contacts.py           poster profile link, phone numbers and WhatsApp links (plain code)
  report.py             build and write the results report
  display.py            terminal-safe formatting (Hebrew handling)
  statuses.py           my status per listing, its history and the editable status list
  feed.py               listing cards for the app (filters, sorting, photos)
  outreach.py           WhatsApp link and opening-message template
  settings.py           app settings (app_settings table)
  auth.py               app login: token + one-time pairing code
  groups.py             the groups to scan (fb_groups table, seeded from config.yaml)
  group_score.py        optional AI relevance rating for groups
  gpt_io.py             export to / import from an external chat AI
  versions.py           edited posts (post_versions)
  prices.py             repeated listings and price history
  followups.py          follow-up reminders
  notify.py             notifications: Web Push, Telegram, quiet hours
  scheduler.py          background scan -> analyze -> notify in the app server
  providers.py          AI provider per stage (Anthropic / OpenAI-compatible), schema check, fallback
  ratelimit.py          per-minute / per-day request limits
  visits.py             visit checklist, rating, notes, photos, comparison
  meetings.py           WhatsApp chat -> checked meeting proposal -> confirmed event
  gcal.py               Google Calendar sign-in and event creation
  config_edit.py        edit config.yaml from the app (ruamel.yaml, backup, validation)
  learning.py           suggest config changes from my statuses and notes
  server.py             FastAPI routes
web/                    the phone app (React + Vite + TypeScript); build with npm run build
tests/                  python -m pytest
```

Created at run time and git-ignored: `apartments.db` (+ backups), `pictures/`, exported
`results*.txt` / `results*.json` / `out.*`, `.scan.lock`, `*.log`, `secrets/` (the app's login
token, Web Push keys, Telegram bot, AI keys, Google sign-in), `uploads/` (your visit photos),
`web/node_modules/`, `web/dist/`.

## Extending

- **Add a user**: copy a `users` entry in `config.yaml`, give it a new `id`, run
  `python analyze_posts.py --all --user <id>`.
- **Changing the AI prompt or schema**: edit `apartment_bot/ai_filter.py` and bump
  `PROMPT_VERSION` or `SCHEMA_VERSION`. Posts then become eligible for re-analysis;
  older analyses stay in the table for comparison.
  Schema limits (structured outputs): no nullable fields - the grammar becomes too
  large. Unknown values use sentinels (`""`, `0`, `-1`) that `normalize_facts()`
  converts to `None`.
- **Add a hard rule**: write a `check_*` function in `hard_rules.py` returning
  `_check(rule, status, detail)` and add it to `run_checks()`; the order there decides
  which failure is reported. Add the rule name to `RULES` in `ai_filter.py`.
- **Schema change**: append a SQL string to `MIGRATIONS` in `db.py`.
- **Add or remove a Facebook group**: tick it in the app's Groups screen (or add it there by
  link). `facebook.groups` in `config.yaml` is only read when the groups table is empty.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `config.yaml not found` | Copy `config.example.yaml` to `config.yaml` and edit it. |
| `API key: the environment variable ... is not set` | Set that variable (open a new terminal afterwards), or use `source: config` - see *API key*. |
| `DLL load failed while importing _ssl: An Application Control policy has blocked this file` in `%USERPROFILE%\.config\browser-harness\tmp\bu-*.log` | Windows **Smart App Control** blocks the unsigned Python that uv downloads. Reinstall browser-harness on the signed Microsoft Store Python 3.12 (see *Quick start*, step 3). Don't disable Smart App Control - it cannot be re-enabled without resetting Windows. |
| `Stopped: Facebook is not logged in in the bot Chrome` | Run `python bot_chrome.py` and log in in that window. |
| `Could not start the bot Chrome` | Check `browser.chrome_path` in `config.yaml`. |
| Every group fails with `Input.dispatchMouseEvent timed out` and 0 posts | Chrome is not drawing the page. The bot Chrome avoids this (minimized windows are parked off-screen). If it still happens, close the bot Chrome window and scan again - it restarts with the right settings. |
| The bot Chrome window can't be seen | It is parked off-screen on purpose. `python bot_chrome.py` brings it back. |
| `Another scan is already running` | Wait for it, or delete `.scan.lock` if no scan is actually running. |
| `SyntaxError: invalid non-printable character U+FEFF` when piping into browser-harness from PowerShell | Windows PowerShell 5.1 adds a BOM. Write the snippet to a BOM-free file and run `cmd /c "browser-harness < file.py"`. (The project scripts are not affected.) |
| Scan reports skipped posts or errors on every group | Facebook may have changed its page structure. The selectors are in `fb_harness_scan.py` (`data-ad-rendering-role` markers, `aria-posinset`, the timestamp link after the author). Check `scan_runs` for the stop reason. |
| General browser-harness problems | `browser-harness --doctor` shows daemon and connection status. |

## License

MIT - see [LICENSE](LICENSE). The software comes without warranty; you are responsible
for how you use it, including compliance with Facebook's terms and privacy law.

## Limitations

- Windows-focused (paths, process checks and the Chrome launcher); other systems would need
  small changes.
- Depends on Facebook's current page structure, which changes without notice.
- Hebrew-first: the prompts and examples are written for Hebrew rental posts in Israel.
- Photos are feed-size thumbnails, and the AI does not look at them yet.
- Not built yet: geocoding, commute times, market comparison, final evaluation, scheduling,
  notifications.
