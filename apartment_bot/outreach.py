"""The app's contact button: a wa.me link with a prefilled opening message, or a fallback.

Uses contacts.py for the phone numbers and WhatsApp links found in the post. Nothing is
sent from here - the link only opens WhatsApp on the phone with the text ready to edit.
"""
import re
import urllib.parse

_SEGMENT = re.compile(r"\[([^\[\]]*)\]")
_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def render_template(template, values):
    """Fill {placeholders}. A [bracketed part] is dropped when any placeholder in it is empty."""
    def segment(m):
        part = m.group(1)
        names = _PLACEHOLDER.findall(part)
        return "" if any(not values.get(n) for n in names) else part
    text = _SEGMENT.sub(segment, template)
    text = _PLACEHOLDER.sub(lambda m: str(values.get(m.group(1)) or ""), text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def template_values(post, facts, group_name):
    """Placeholder values for one post. facts may be None (post never analyzed)."""
    facts = facts or {}
    rent = facts.get("rent_ils")
    return {
        "name": (post["author"] or "").split()[0] if post["author"] else "",
        "area": facts.get("neighborhood") or facts.get("city") or "",
        "street": facts.get("street") or "",
        "group": group_name or "",
        "price": f"{rent:,}" if rent else "",
    }


def whatsapp_number(contact):
    """International number (9725XXXXXXXX) for WhatsApp, or None.

    A WhatsApp link in the post wins; otherwise the first mobile number (05X). Landlines
    are skipped - they rarely have WhatsApp.
    """
    for link in contact["whatsapp"]:
        digits = link.rsplit("/", 1)[-1]
        if digits.isdigit():
            return digits
    for phone in contact["phones"]:
        digits = phone.replace("-", "")
        if digits.startswith("05"):
            return "972" + digits[1:]
    return None


def contact_action(contact, message):
    """What the contact button does: {"kind": "whatsapp" | "facebook" | "none", "url": ...}."""
    number = whatsapp_number(contact)
    if number:
        return {"kind": "whatsapp", "url": f"https://wa.me/{number}?text={urllib.parse.quote(message)}",
                "number": number}
    for s in contact.get("suspect") or []:   # a mistyped number with one obvious fix: offered as a guess
        guess = (s["guess"] or "").replace("-", "")
        if guess.startswith("05"):
            number = "972" + guess[1:]
            return {"kind": "whatsapp", "url": f"https://wa.me/{number}?text={urllib.parse.quote(message)}",
                    "number": number, "guessed": True, "typed": s["raw"], "guess": s["guess"]}
    if contact["facebook_profile"]:
        return {"kind": "facebook", "url": contact["facebook_profile"]}
    return {"kind": "none", "url": None}
