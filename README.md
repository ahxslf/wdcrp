# 🛡️ ModBot — Discord Auto-Moderation Bot

A single-server moderation bot built exactly to your rules:

| Rule | Behavior |
|---|---|
| **Profanity** | ✅ **Allowed.** The bot never punishes normal swearing — even a lone "motherfucker" or "son of a bitch" is fine. Only **extreme profanity spam** is flagged (weighted scoring, `profanity.py`), and the matcher is **bypass-resistant**: `bittttch`, `b i t c h`, `b.i.t.c.h`, `b1tch`, `phuck`, `fuck y0u`, Cyrillic lookalikes (`bіtch`), `m0th3rfuck3r`, and even sending one letter per message (`b`, `i`, `t`, `c`, `h`) are all detected. Cross-message profanity flooding is caught too. |
| **Emoji** | ✅ Normal emoji use is fine. Excessive/repeated emoji spam → **warning** → timeout → escalation if it continues. |
| **Word/message spam** | Repeated messages, repeated words, character spam (`aaaaaa…`, `asdfasdf…`), and message flooding → **warning** → timeout, with **increasing durations** for repeat offenders. |
| **Founder pings** (@Foundership Team role **or its members**) | ⚠️ Shows a warning **every single time** (even on the 20th ping). **Never** warns, times out, kicks, or bans. It's just a reminder. **Exempt:** Foundership Team members themselves, and anyone who can view the **staff chat** channel. |
| **Invites** | 🔗 Message is **deleted**. 1st offense → **24h timeout**. Any further offense → **kick** (the only auto-kick in the bot). **Never bans.** Fully logged. Staff can whitelist approved links. |

### Warning system

- **3 warnings (small stuff) → timeout.** Base timeout is **1 hour**.
- Timeout ladder for repeat offenders: **1h → 3h → 6h → 12h → 24h**, resetting back to 1h after **7 clean days**.
- Small (non-serious) warnings **expire 24 hours** after they were issued — per-warning rolling expiry, so old warnings fall off automatically.
- The **Warning 1 / 2 / 3 roles are assigned automatically** to show a member's current warning count, and removed when warnings expire or are cleared.
- Everyone is moderated equally — **staff and the owner are NOT exempt** from the auto-mod (only bots are ignored).

### Log channels (already configured)

All four log types (warnings, timeouts, spam, invites) go to **one** channel:

| Purpose | Channel ID |
|---|---|
| All moderation logs | `1438315087264874636` |

### Roles & IDs (already configured)

| Purpose | ID |
|---|---|
| Warning 1 | `1550529385998581841` |
| Warning 2 | `1550529432324931686` |
| Warning 3 | `1550529460607127753` |
| Foundership Team ("founder" role) | `1475159680480182424` |
| Staff chat channel (founder-ping exemption) | `1411737068765446214` |
| Guild | `1392033748454735902` |

---

## 🚀 Setup (5–10 minutes)

### 1. Create the bot application

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) → **New Application**.
2. Open the **Bot** tab → **Reset Token** → copy the token (keep it secret!).
3. **Turn ON both Privileged Gateway Intents** on that same page:
   - ✅ `Server Members Intent`
   - ✅ `Message Content Intent`

### 2. Invite the bot to your server

Go to **OAuth2 → URL Generator**, select scopes `bot` and `applications.commands`,
and check these permissions:

- Kick Members
- Manage Roles
- Manage Messages
- Moderate Members (Timeout)
- Change Nickname *(optional — used to set the in-server nickname to "DCRP | Utilities")*
- View Channel, Send Messages, Embed Links, Read Message History, Add Reactions

(Equivalent permission integer: `1099847265346` —
`https://discord.com/oauth2/authorize?client_id=<YOUR_CLIENT_ID>&permissions=1099847265346&scope=bot%20applications.commands`)

**Bot identity:** the account's display *name* is set in the Developer Portal
(Bot → Username) — set it to **DCRP | Utilities** there. The bot
automatically shows a **"Watching the server"** activity status and tries to
set its in-server nickname on startup.

> ⚠️ **Role hierarchy matters:** In Server Settings → Roles, drag the bot's role **above** the Warning 1/2/3 roles (and above any member it might need to time out or kick). The bot can't act on members whose highest role is above its own.

### 3. Install & configure

```bash
pip install -r requirements.txt
cp .env.example .env
# edit .env: paste DISCORD_BOT_TOKEN, and your DISCORD_GUILD_ID
```

The `.env` file is loaded automatically on startup (via `python-dotenv`, which
is in `requirements.txt`). Alternatively you can `export` the variables in
whatever environment runs the bot (systemd unit, Docker, pm2, etc.).

### 4. Run

```bash
cd modbot
python bot.py
```

You should see:

```
[ModBot] Slash commands synced to guild <your guild id>
[ModBot] Logged in as ModBot#1234
```

Keep it alive on a VPS with `tmux`, `screen`, `systemd`, or a process manager
like `pm2 start bot.py --interpreter python3`.

---

## 🧰 Staff slash commands

Visible only to members with the **Moderate Members** permission:

| Command | Purpose |
|---|---|
| `/invites-exempt add <link>` | Whitelist an invite link (e.g. a partner server). Exempt links never trigger invite moderation. |
| `/invites-exempt remove <code>` | Remove a link from the exemption list. |
| `/invites-exempt list` | Show all exempt links. |
| `/warnings <member>` | View a member's active warnings, invite offenses, and timeout-ladder rung. |
| `/clearwarnings <member>` | Clear a member's active small warnings + remove Warning roles. |
| `/mod-status` | Show the current thresholds and configuration. |

💡 Invites that point back at **your own server** are automatically ignored — no need to exempt them.

---

## ⚙️ Tuning (no coding needed)

Every threshold lives in **`config.py`** with comments:

- `MAX_EMOJI_PER_MESSAGE`, `MAX_SAME_EMOJI` — emoji spam sensitivity
- `FLOOD_MAX_MESSAGES`/`FLOOD_WINDOW_SECONDS` — flood sensitivity
- `REPEAT_MSG_COUNT`, `CHAR_RUN_LIMIT`, `WORD_REPEAT_LIMIT` — spam patterns
- `PROFANITY_SPAM_MIN_SCORE`, `PROFANITY_SPAM_MIN_RATIO`, `PROFANITY_EXTREME_MIN`,
  `PROFANITY_HISTORY_*`, `PROFANITY_STITCH_*` — when profanity becomes extreme spam
- `TIMEOUT_LADDER_MINUTES` — the escalation ladder
- `WARNING_EXPIRY_HOURS` — the 24h warning reset
- `WARNINGS_UNTIL_TIMEOUT` — 3 by default
- `DEBUG` — console debug output (off by default)

Restart the bot after changing config.

---

## ✅ Guarantee checklist (matches your spec)

- [x] Profanity allowed; only *spammy* extreme profanity is caught (as normal spam).
- [x] Emoji spam → warn → timeout → escalation.
- [x] Repeated words/messages/character spam/flooding → warn → timeout → escalation ladder.
- [x] Founder pings → warning message **every time**, **zero** punishment, no kick/ban ever.
- [x] Invites → **deleted**, 1st: 24h timeout, 2nd+: **kick**, never ban, full logging.
- [x] Staff-configurable exempt invite links.
- [x] 3 warnings → 1h timeout baseline; escalates for repeat offenders.
- [x] Warnings reset after 24h (rolling) unless serious (invite records persist).
- [x] Warning 1/2/3 roles auto-assigned/removed; timeout, warning, spam and invite logs go to the specified channels.

## 🧬 Profanity engine (what counts as "extreme")

Normal profanity is **never** punished. Detection lives in `profanity.py`:

- **Mild terms (weight 1):** fuck, shit, bitch, ass, whore, slut… — alone or in
  normal sentences they do nothing.
- **Extreme terms (weight 2):** motherfucker, son of a bitch, fuck you, cunt,
  kys, slurs, self-harm phrases… — a single use is *still* allowed.
- A message becomes **profanity spam** when weighted score ≥ 6 *and* ≥ 50% of
  the words are profane, **or** it contains **3+ extreme insults** at once.
  Example: `son of a bitch motherfucker fuck you` → 3 extreme → flagged.
- **Escapes don't work:** stretched letters (`bittttch`), separators
  (`b i t c h`, `b.i.t.c.h`, `f_u_c_k`), leetspeak (`b1tch`, `fuck y0u`,
  `m0th3rfuck3r`, `$lvt`), letter swaps (`phuck`, `fvck`), Cyrillic/Greek
  lookalikes (`bіtch`, `сunt`), joined words (`fuckyou`, `mother fucker`)
  and **one-letter-per-message** tricks (`b`,`i`,`t`,`c`,`h` in 5 messages)
  are all caught — without false-positiving on "Scunthorpe", "class",
  "as soon as possible", "shitake mushrooms", etc. (all covered by tests).
- The mild/extreme term lists are plain text at the top of `profanity.py` —
  add or remove words anytime, then restart the bot.

## 🧪 Tests

The detection engine is fully unit-tested (no Discord connection needed):

```bash
python test_detectors.py
```
