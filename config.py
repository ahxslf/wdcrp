"""
DCRP | Utilities — configuration.

Auto-moderation for the Washington D.C. Roleplay server.
All server IDs, thresholds, and behavior knobs live here so the bot can
be tuned without touching the logic.
"""

import os

# Load .env automatically when python-dotenv is installed (recommended).
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ----------------------------------------------------------------------------
# REQUIRED SETUP (via environment / .env file)
# ----------------------------------------------------------------------------
BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
# Optional but recommended: set your server (guild) ID for INSTANT slash
# command sync. Without it, commands still work but global sync can take
# up to ~1 hour.
# Default = the real Washington D.C. Roleplay server.
# DISCORD_GUILD_ID in .env overrides this (e.g. for a test server).
DEFAULT_GUILD_ID = 1392033748454735902
_GUILD_RAW = (os.environ.get("DISCORD_GUILD_ID") or "").strip()
GUILD_ID = int(_GUILD_RAW) if _GUILD_RAW.isdigit() else DEFAULT_GUILD_ID

# ----------------------------------------------------------------------------
# BRANDING
# ----------------------------------------------------------------------------
BOT_NAME = "DCRP | Utilities"           # also set as the in-server nickname
SERVER_NAME = "Washington D.C. Roleplay"
# Custom status text — shown VERBATIM everywhere (member list AND profile).
# (A typed "Watching" activity hides the verb in the member list on modern
# Discord clients, so we use a custom status with the full text instead.)
ACTIVITY_TEXT = "Watching the server"

# ----------------------------------------------------------------------------
# ROLE IDs  (real server)
# ----------------------------------------------------------------------------
WARNING_ROLE_IDS = (
    1550529385998581841,  # Warning 1
    1550529432324931686,  # Warning 2
    1550529460607127753,  # Warning 3
)
# "Foundership Team" role — pinging it (or its members) shows the reminder.
FOUNDER_ROLE_ID = 1475159680480182424

# ----------------------------------------------------------------------------
# LOG CHANNEL
# ----------------------------------------------------------------------------
# All moderation logs (warnings, timeouts, spam, invites) go to ONE channel.
LOG_CHANNEL_ID = 1438315087264874636
WARN_LOG_CHANNEL_ID = LOG_CHANNEL_ID     # warning logs
TIMEOUT_LOG_CHANNEL_ID = LOG_CHANNEL_ID  # timeout logs
SPAM_LOG_CHANNEL_ID = LOG_CHANNEL_ID     # spam detections
INVITE_LOG_CHANNEL_ID = LOG_CHANNEL_ID   # invite logs

# Staff-only chat channel. Members who can VIEW this channel are allowed to
# ping the Foundership Team role / its members without getting the reminder.
# Set to 0 to disable the exemption.
STAFF_CHAT_CHANNEL_ID = 1411737068765446214

# ----------------------------------------------------------------------------
# WARNING SYSTEM
# ----------------------------------------------------------------------------
# Small stuff: 3 active warnings -> timeout (1st rung of the ladder).
WARNINGS_UNTIL_TIMEOUT = 3

# Non-serious warnings expire 24h after they were issued (rolling window).
WARNING_EXPIRY_HOURS = 24

# Serious records (invite offenses, timeout escalation) persist this long.
SERIOUS_EXPIRY_DAYS = 30

# Timeout ladder for repeat offenders. Rung 0 applies when the 3-warnings
# threshold is reached the first time (1 hour — the "small stuff" timeout);
# each time the user completes another 3-warning cycle they move up a rung.
# The ladder resets back to rung 0 after ESCALATION_RESET_DAYS clean days.
TIMEOUT_LADDER_MINUTES = (60, 180, 360, 720, 1440)  # 1h, 3h, 6h, 12h, 24h
ESCALATION_RESET_DAYS = 7

# Prevents one rapid burst from burning several warnings in seconds.
# A user can receive at most one auto-warning every N seconds.
WARN_COOLDOWN_SECONDS = 8

# ----------------------------------------------------------------------------
# INVITES
# ----------------------------------------------------------------------------
# First detected invite -> this timeout. Second (or any later) -> kick.
INVITE_TIMEOUT_HOURS = 24

# ----------------------------------------------------------------------------
# SPAM DETECTION THRESHOLDS
# ----------------------------------------------------------------------------
# Message flooding
FLOOD_MAX_MESSAGES = 6      # 6+ messages...
FLOOD_WINDOW_SECONDS = 6    # ...within a 6 second window

# Repeated identical messages
REPEAT_MSG_COUNT = 3        # 3 identical messages...
REPEAT_MSG_WINDOW_SECONDS = 60

# Character spam
CHAR_RUN_LIMIT = 12         # same character repeated 12+ times in a row
LOW_DIVERSITY_MIN_LEN = 24  # long message...
LOW_DIVERSITY_MAX_UNIQUE = 4  # ...built from <= 4 distinct characters

# Word spam
WORD_REPEAT_LIMIT = 6       # the same word repeated 6+ times in a message

# Emoji spam (a handful of emojis per message is totally fine)
MAX_EMOJI_PER_MESSAGE = 10  # 11+ emojis in one message = spam
MAX_SAME_EMOJI = 6          # the SAME emoji 7+ times = spam

# Profanity: profanity itself is ALLOWED — a lone insult, even
# "motherfucker" or "son of a bitch", triggers NOTHING. Only extreme
# profanity SPAM is flagged (see profanity.py for the weighted engine).
#
# Single-message rule: weighted score >= MIN_SCORE AND >= MIN_RATIO of the
# message is profanity  — OR — at least EXTREME_MIN weight-2 insults at once
# (mild words weigh 1, extreme insults weigh 2).
PROFANITY_SPAM_MIN_SCORE = 6
PROFANITY_SPAM_MIN_RATIO = 0.5
PROFANITY_EXTREME_MIN = 3

# Cross-message rule: combined profanity score across the user's recent
# messages. Catches steady profanity flooding that stays under the
# single-message threshold on each individual message.
PROFANITY_HISTORY_WINDOW = 60        # seconds
PROFANITY_HISTORY_MIN_SCORE = 12
PROFANITY_HISTORY_MIN_MESSAGES = 4

# Letter-by-letter bypass: "b", "i", "t", "c", "h" sent as 5 messages.
PROFANITY_STITCH_MAX_GAP = 15        # max seconds between letter-messages
PROFANITY_STITCH_MIN_LETTERS = 4     # need at least this many to qualify

# ----------------------------------------------------------------------------
# GENERAL BEHAVIOR
# ----------------------------------------------------------------------------
# Staff bypass DISABLED per owner's request: the bot moderates every member
# equally, including staff and the server owner.
STAFF_BYPASS = False

# Try to DM users when they are timed out / kicked.
NOTIFY_USER_IN_DM = True

# Where per-user data (warnings, offense counts, exempt invites) is stored.
DB_PATH = os.environ.get("MODBOT_DB_PATH", "modbot.db")

# Console debug output (per-message decisions). Kept OFF — flip to True only
# when troubleshooting.
DEBUG = False
