"""Contact details: the poster's Facebook profile, plus phone numbers and WhatsApp
links found in the post text. Plain code, no AI."""
import re

# A phone-like run: starts with 0 / 972 / +972, then digits with optional - . or spaces.
_PHONE_RUN = re.compile(r"(?<![\d/])(?:\+?972|0)[\d\-\s.]{7,14}\d(?!\d)")
_WHATSAPP = re.compile(r"(?:https?://)?(?:wa\.me/|api\.whatsapp\.com/send/?\?phone=)\+?(\d{8,13})", re.I)


_LEADING_O = re.compile(r"(?<![A-Za-z])[Oo](?=\d\d)")
_MIDDLE_O = re.compile(r"(?<=[\d\-])[Oo](?=[\d\-])|(?<=\d)[Oo](?=\s|$)")


def _o_to_zero(text):
    """People type the letter O for the digit 0 ("O52-883O311"): read it as a zero when it sits inside a number."""
    text = _LEADING_O.sub("0", text or "")
    for _ in range(3):    # "O5O-" style runs need more than one pass
        text = _MIDDLE_O.sub("0", text)
    return text


def _normalize(digits):
    """Return an Israeli number as 05X-XXXXXXX / 0X-XXXXXXX, or None if not valid."""
    if digits.startswith("972"):
        digits = "0" + digits[3:]
    elif not digits.startswith("0") and len(digits) in (8, 9):
        digits = "0" + digits                      # wa.me links sometimes drop the 972/0
    if len(digits) == 10 and digits.startswith("05"):
        return f"{digits[:3]}-{digits[3:]}"
    if len(digits) == 9 and digits[1] in "23489":   # landlines: 02, 03, 04, 08, 09
        return f"{digits[:2]}-{digits[2:]}"
    return None


def phones_in_text(text):
    """Distinct phone numbers mentioned in the text, in order of appearance."""
    found = []
    for m in _PHONE_RUN.finditer(_o_to_zero(text)):
        num = _normalize(re.sub(r"\D", "", m.group(0)))
        if num and num not in found:
            found.append(num)
    return found


def suspect_phones(text):
    """Number-like runs that are not valid Israeli numbers, each with a guess at what was meant.

    {"raw": "095-4563-4144", "guess": "054-563-4144" | None}. A guess exists only when deleting
    one single digit gives exactly one valid number (a typo like a stray extra digit); the app
    then offers WhatsApp for it, clearly marked as a guess, and never sends anything itself.
    """
    out = []
    for m in _PHONE_RUN.finditer(_o_to_zero(text)):
        digits = re.sub(r"\D", "", m.group(0))
        if _normalize(digits) or len(digits) not in (9, 11, 12):
            continue
        fixes = {_normalize(digits[:i] + digits[i + 1:]) for i in range(len(digits))} - {None}
        out.append({"raw": m.group(0).strip(), "guess": next(iter(fixes)) if len(fixes) == 1 else None})
    return out


def whatsapp_links(text):
    """Distinct WhatsApp links in the text, as https://wa.me/972... URLs."""
    out = []
    for m in _WHATSAPP.finditer(text or ""):
        num = _normalize(m.group(1))
        link = f"https://wa.me/972{num.replace('-', '')[1:]}" if num else f"https://wa.me/{m.group(1)}"
        if link not in out:
            out.append(link)
    return out


def profile_url(author_id):
    """Link to the poster's Facebook profile, or None for anonymous/page posts."""
    return f"https://www.facebook.com/profile.php?id={author_id}" if author_id else None


def contact_info(post):
    """Name, profile, phones and WhatsApp links, plus `best`: phone, else WhatsApp, else profile."""
    info = {
        "name": post["author"],
        "facebook_profile": profile_url(post["author_id"]),
        "phones": phones_in_text(post["text"]),
        "whatsapp": whatsapp_links(post["text"]),
        "suspect": suspect_phones(post["text"]),
    }
    if info["phones"]:
        info["best"] = f"phone {info['phones'][0]}"
    elif info["whatsapp"]:
        info["best"] = f"WhatsApp {info['whatsapp'][0]}"
    elif info["facebook_profile"]:
        info["best"] = f"Facebook {info['facebook_profile']}"
    else:
        info["best"] = "message via the post (anonymous or page post)"
    return info
