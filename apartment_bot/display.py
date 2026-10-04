"""Terminal-safe output.

Windows consoles render Hebrew left-to-right, which reverses it. Terminal output
therefore replaces Hebrew runs with a marker; the original text is untouched in
SQLite and in JSON exports (open those in an editor to read Hebrew correctly).
"""
import re

_HEBREW_RUN = re.compile(r"[֐-׿יִ-ﭏ][֐-׿יִ-ﭏ\s\d\"'.,:;!?()\-/+]*")


def term_safe(s):
    """Replace Hebrew runs with "[Hebrew]" for console output; None becomes "-"."""
    if s is None:
        return "-"
    return _HEBREW_RUN.sub(lambda m: "[Hebrew]" + (" " if m.group(0).endswith(" ") else ""), str(s))


def keep_text(s):
    """Like term_safe but keeps Hebrew - for UTF-8 files."""
    return "-" if s is None else str(s)


def fmt_money(v):
    return "-" if v is None else f"{v:,} ILS"


def facts_lines(f, term_safe=term_safe):
    """Format normalized extracted facts as short report lines.

    Terminal-safe by default; pass term_safe=keep_text to keep Hebrew (for files).
    """
    rm = f["roommates"]
    if f.get("_partial"):   # quick first pass: only these facts were extracted
        return [f"type: {f['listing_type']}   rental offer: {f['is_rental_offer']}   "
                f"(quick pass: other details not extracted)",
                f"rent: {fmt_money(f['rent_ils'])} ({f['rent_scope']})   "
                f"total known: {fmt_money(f['total_monthly_ils_known'])}",
                f"location: {term_safe(f['location_summary_en'])}",
                f"roommates: required gender {rm['required_gender']} ({rm['gender_strictness']}), "
                f"age {rm['age_min'] or ''}-{rm['age_max'] or ''} ({rm['age_strictness']})"]
    extras = ", ".join(
        f"{c['kind']} {fmt_money(c['amount_ils'])}" + (" (included)" if c["included_in_rent"] == "yes" else "")
        for c in f["additional_costs"]) or "-"
    age = "-"
    if rm["age_min"] is not None or rm["age_max"] is not None:
        age = f"{rm['age_min'] or ''}-{rm['age_max'] or ''} ({rm['age_strictness']})"
    return [
        f"type: {f['listing_type']}   rental offer: {f['is_rental_offer']}   confidence: {f['extraction_confidence']}",
        f"rent: {fmt_money(f['rent_ils'])} ({f['rent_scope']})   extras: {extras}   "
        f"total known: {fmt_money(f['total_monthly_ils_known'])}",
        f"deposit/guarantees: {term_safe(f['deposit_and_guarantees'])}",
        f"location: {term_safe(f['location_summary_en'])}",
        f"rooms: {f['rooms'] or '-'}   size: {f['size_sqm'] or '-'} sqm   floor: {f['floor'] if f['floor'] is not None else '-'}"
        f"/{f['building_floors'] or '-'}   elevator: {f['elevator']}   balcony: {f['balcony']}   "
        f"parking: {f['parking']}   shelter: {f['shelter']}",
        f"furnished: {f['furnished']}   broker: {f['broker']}   pets: {f['pets']}   smoking: {f['smoking']}",
        f"move-in: {term_safe(f['move_in_date'])}   lease: {term_safe(f['lease_duration'])}",
        f"roommates: count {rm['current_roommate_count'] if rm['current_roommate_count'] is not None else '-'}, "
        f"required gender {rm['required_gender']}, preferred {rm['preferred_gender']} "
        f"({rm['gender_strictness']}), age {age}",
        f"positives: {term_safe('; '.join(f['positives'])) or '-'}",
        f"negatives: {term_safe('; '.join(f['negatives'])) or '-'}",
        f"missing: {term_safe('; '.join(f['missing_important_info'])) or '-'}",
        f"ambiguities: {term_safe('; '.join(f['ambiguities'])) or '-'}",
    ]
