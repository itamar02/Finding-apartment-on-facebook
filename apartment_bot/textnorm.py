"""Text normalisation shared by the checks that compare posts.

Facebook shows emoji as little pictures; the scanner now keeps them in the text (older posts
were stored without them). Comparisons - "is this the same text?", "was the post edited?" -
therefore ignore emoji and spacing, so an old copy without emoji still matches a new copy
with them.
"""
import re

_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF☀-➿⬀-⯿←-⇿⌀-⏿①-⓿"
    "©®‼⁉™ℹ〰〽㊗㊙︎️‍⃣‎‏]")
_SPACES = re.compile(r"\s+")


def strip_emoji(text):
    return _EMOJI.sub("", text or "")


def normalize(text):
    """Text for comparing posts: no emoji, single spaces, trimmed."""
    return _SPACES.sub(" ", strip_emoji(text)).strip()
