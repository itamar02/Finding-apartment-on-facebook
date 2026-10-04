"""Stage 1: AI extraction + initial filter (Claude via the Anthropic API).

One request per post. The model reads the raw post text, the post date and the
user's configuration, extracts structured facts, checks each configured rule,
and returns REJECT / PASS / NEEDS_REVIEW. Output is constrained to SCHEMA with
structured outputs, so it always parses.

Bump PROMPT_VERSION or SCHEMA_VERSION whenever the prompt or schema changes;
that makes previously analyzed posts eligible for re-analysis.
"""
import json

import anthropic

PROMPT_VERSION = "2"  # 2: preferred / less_preferred areas
SCHEMA_VERSION = "1"
PIPELINE_VERSION = f"prompt-{PROMPT_VERSION}.schema-{SCHEMA_VERSION}"

# USD per million tokens.
PRICING = {
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00, "cache_write": 1.25, "cache_read": 0.10},
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00, "cache_write": 2.50, "cache_read": 0.20},
    "claude-opus-5-5": {"input": 4.00, "output": 20.00, "cache_write": 5.00, "cache_read": 0.20},
}

# Rule identifiers shared by the AI output and the hard-rule checks in code.
RULES = [
    "not_a_rental_offer", "listing_type", "gender_eligibility", "age_eligibility",
    "rent_absolute_max", "rent_preferred_max", "search_city", "excluded_area",
    "broker_policy", "required_features", "dealbreakers", "move_in_window",
    "roommate_count", "roommate_age_preference", "post_age", "duplicate",
    "free_text_preferences", "other",
]


# Nullable (union-typed) fields make the compiled grammar too large for structured
# outputs, so the schema has none. Unknown values use sentinels the model is told
# about - "" for text, 0 where 0 is impossible, -1 for counts - and normalize_facts()
# turns them back into None before anything is stored or checked.
_TEXT = {"type": "string"}
_INT = {"type": "integer"}
_NUM = {"type": "number"}


def _enum(*values):
    return {"type": "string", "enum": list(values)}


def _obj(props):
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


_STR_LIST = {"type": "array", "items": {"type": "string"}}
_YNU = _enum("yes", "no", "unknown")
_GENDER = _enum("male", "female", "any", "unknown")
_STRICTNESS = _enum("strict", "preference", "not_stated")

FACTS_SCHEMA = _obj({
    "is_rental_offer": {"type": "boolean"},
    "listing_type": _enum("whole_apartment", "roommate_search", "room_replacement", "sublet", "other", "unknown"),
    "listing_type_evidence": _TEXT,
    "rent_ils": _INT,
    "rent_scope": _enum("whole_apartment", "single_room_or_share", "unknown"),
    "additional_costs": {"type": "array", "items": _obj({
        "kind": _enum("arnona", "vaad_bayit", "utilities", "bills_bundle", "internet", "cleaning", "other"),
        "amount_ils": _INT,
        "included_in_rent": _YNU,
        "note": _TEXT,
    })},
    "deposit_and_guarantees": _TEXT,
    "city": _TEXT,
    "neighborhood": _TEXT,
    "street": _TEXT,
    "location_hints": _STR_LIST,
    "location_summary_en": _TEXT,
    "rooms": _NUM,
    "size_sqm": _NUM,
    "floor": _TEXT,
    "building_floors": _INT,
    "elevator": _YNU,
    "balcony": _YNU,
    "parking": _YNU,
    "shelter": _enum("mamad", "mamak", "building_shelter", "none", "unknown"),
    "furnished": _enum("furnished", "partially_furnished", "unfurnished", "unknown"),
    "broker": _enum("no_broker", "broker", "unknown"),
    "broker_fee": _TEXT,
    "move_in_date": _TEXT,
    "move_in_flexible": _YNU,
    "lease_duration": _TEXT,
    "lease_months": _INT,
    "pets": _enum("allowed", "not_allowed", "negotiable", "unknown"),
    "smoking": _enum("allowed", "not_allowed", "unknown"),
    "roommates": _obj({
        "current_roommate_count": _INT,
        "current_roommate_genders": _STR_LIST,
        "required_gender": _GENDER,
        "preferred_gender": _GENDER,
        "gender_strictness": _STRICTNESS,
        "gender_evidence": _TEXT,
        "age_min": _INT,
        "age_max": _INT,
        "age_strictness": _STRICTNESS,
        "age_evidence": _TEXT,
    }),
    "positives": _STR_LIST,
    "negatives": _STR_LIST,
    "missing_important_info": _STR_LIST,
    "ambiguities": _STR_LIST,
    "extraction_confidence": _enum("high", "medium", "low"),
})

SCHEMA = _obj({
    "facts": FACTS_SCHEMA,
    "rule_checks": {"type": "array", "items": _obj({
        "rule": _enum(*RULES),
        "status": _enum("pass", "fail", "soft_fail", "unknown", "not_applicable"),
        "evidence": _TEXT,
        "explanation": {"type": "string"},
    })},
    "decision": _obj({
        "decision": _enum("REJECT", "PASS", "NEEDS_REVIEW"),
        "rule": _enum("none", *RULES),
        "reason": {"type": "string"},
    }),
})

SYSTEM_PROMPT = """\
You are the first-stage filter of an apartment-hunting assistant in Israel. You receive one raw \
Facebook group post (usually Hebrew, informal) plus the configuration of the person searching. \
You do two jobs: extract structured facts from the post, then decide whether the listing survives \
the first filter for this person.

Read the post the way a native Hebrew speaker would. Do not rely on keyword matching; interpret \
meaning, slang, abbreviations and context.

## Listing types
- whole_apartment: an entire apartment for rent, the tenant gets the whole unit.
- roommate_search: current tenants look for a new long-term roommate for a room in their shared \
apartment (e.g. "מחפשים שותף/ה", "מתפנה חדר בדירת שותפים").
- room_replacement: a leaving tenant hands over their own room/contract in a shared apartment \
("מחליף/ה", "עוזבת ומחפשת מחליפה", "כניסה לחוזה").
- sublet: temporary stay, typically weeks to a few months ("סאבלט", "לחודשיים").
- other: not one of the above (e.g. a unit in a house - describe in listing_type_evidence).
is_rental_offer is false for posts that are not offering a place: people searching for an \
apartment, furniture sales, ads for services, etc.

## Extraction rules
- rent_ils is the base monthly rent the tenant would pay for what is offered: the whole apartment \
for whole_apartment, the room/share for roommate listings. Set rent_scope accordingly.
- Never fold extra costs into rent or drop them. "3,300 + 700 ועד וחשבונות" means rent_ils 3300 and \
one additional_costs entry of 700 (kind bills_bundle, note "vaad bayit and bills"). List every \
monthly extra that is mentioned, even without an amount (amount_ils 0 = amount not stated). Totals \
are computed later by code, so do not add anything up yourself.
- "כולל" / "כולל הכל" / "כולל חשבונות" means the named costs have included_in_rent "yes".
- Location: copy city, neighborhood and street as written in the post (original language). Put \
relative location phrases in location_hints, e.g. "10 דקות מעזריאלי" or "ליד הקאנטרי" - these are \
location information even without a street. location_summary_en is a short English description.
- broker: "ללא תיווך", "בלי תיווך", "ללא דמי תיווך", "ישירות מבעל הדירה" mean no_broker. Mentions of \
an agency, "תיווך", "דמי תיווך", or a realtor posting for a client mean broker.
- Roommate gender: distinguish a strict requirement from a preference.
  "מחפשות שותפה" (feminine plural verb seeking a feminine roommate) = required_gender female, strict.
  "רק בנות" / "לשותפה בלבד" = female, strict. "עדיפות לבת" / "עדיף שותפה" = preferred_gender female, \
  strictness preference, required_gender any. "שותף/ה" = any.
  Hebrew grammatical gender of the seekers' own verbs ("מחפשים", "מחפשות") describes them, and \
  together with the gender of the sought noun ("שותף", "שותפה") signals what they want.
- Roommate age: "מחפשים מישהו בן 30+" = age_min 30, strict. "בטווח 25-30" = strict unless softened. \
"עדיפות לגילאי 25-30" = preference. Use not_stated when there is no age language.
- *_evidence fields hold a short verbatim quote from the post (original language) supporting the \
value, or null.
- positives / negatives / missing_important_info / ambiguities: short English phrases, relevant to \
this person's configuration. Missing info means things a renter would need that the post omits \
(price, location, move-in date, roommate details ...).
- Never invent facts. Unknown values: text fields "" (empty string); enums "unknown"; rent_ils, \
amount_ils, rooms, size_sqm, building_floors, lease_months, age_min, age_max 0; \
current_roommate_count -1. floor is text: the floor number as digits ("0" for ground floor / קרקע, \
"-1" for basement / מרתף), or "" if not stated.

## Decision rules
Return one rule_checks entry for each rule that is relevant to this post, then the decision.
- REJECT only when explicit evidence in the post violates a hard requirement of the configuration:
  not a rental offer; listing type not in allowed_types; strict gender requirement the person does \
  not meet; strict age range that excludes the person's age; rent (or rent plus known costs, per \
  budget.absolute_max_applies_to) clearly above budget.absolute_max_rent; located outside \
  search_cities or in an excluded area; an explicit dealbreaker; broker when broker.policy is \
  reject; a required feature explicitly absent; move-in date clearly outside the move_in window.
- NEEDS_REVIEW when a hard requirement may be violated but the wording is ambiguous, or when \
  roommates.gender_preference_mismatch is needs_review and the advertisers merely prefer the other \
  gender.
- PASS otherwise. Missing information alone is not a reason to reject; list it in \
  missing_important_info. Soft preferences (preferred_max_rent, nice_to_have, broker.policy avoid, \
  areas.preferred, areas.less_preferred, free_text) never cause REJECT; record them as soft_fail \
  checks and in negatives (e.g. "located in Ramat Gan, which is less preferred"), or in positives \
  when a listing is in a preferred area.
- decision.rule names the single rule that drives a REJECT or NEEDS_REVIEW ("none" for PASS).
- decision.reason is one concrete English sentence a person can audit later, stating the evidence \
  and the configured value, e.g. "Rejected because the advertisers explicitly require a female \
  roommate (\\"מחפשות שותפה\\") and the configured user is male." or "Rejected because the listing \
  requires age 30+ and the configured user age is 23."

## Out of scope at this stage
- Do not judge whether the price is good or bad for the market.
- Do not estimate commute times or distances; only extract location information. Never reject \
  for commute reasons.
"""

# Parts of the user config the model needs (commute is context only at this stage).
_CONFIG_KEYS = ["profile", "listing", "budget", "areas", "broker", "roommates", "features",
                "move_in", "commute", "free_text"]


def user_config_block(user):
    """The user's config as deterministic JSON (sorted keys keep the prompt cache valid)."""
    relevant = {k: user[k] for k in _CONFIG_KEYS if k in user}
    return ("## Configuration of the person searching\n"
            + json.dumps(relevant, ensure_ascii=False, sort_keys=True, indent=1, default=str))


def cost_usd(model, usage):
    """Approximate request cost in USD from token usage; None for models not in PRICING."""
    p = PRICING.get(model)
    if not p:
        return None
    return (usage["input_tokens"] * p["input"] + usage["output_tokens"] * p["output"]
            + usage["cache_write_tokens"] * p["cache_write"]
            + usage["cache_read_tokens"] * p["cache_read"]) / 1_000_000


class AnalysisError(Exception):
    """The model answered but unusably (refusal, max_tokens). args: (message, usage, cost)."""


def analyze_post(client, model, user, post_text, posted_at, today, mode="full"):
    """Run one post through the model. Returns (parsed_output, raw_text, usage, cost).

    mode="full": every fact plus per-rule checks. mode="triage": a short first pass -
    just the decision and the few facts the code checks need (see TRIAGE_SCHEMA).
    mode="gate": the cheapest pass - only "is it a rental offer?" and the listing type
    (see GATE_SCHEMA); the code decides what to do with the answer.
    """
    schema = {"full": SCHEMA, "triage": TRIAGE_SCHEMA, "gate": GATE_SCHEMA}[mode]
    instruction = {"full": "", "triage": TRIAGE_INSTRUCTION, "gate": GATE_INSTRUCTION}[mode]
    response = client.messages.create(
        model=model,
        # A ceiling, not a cost: Haiku answers in ~1,000 tokens, but models that think
        # first (Sonnet 5.5 / Opus 5.5) count their thinking toward this limit.
        max_tokens=16000,
        system=[
            {"type": "text", "text": SYSTEM_PROMPT},
            # The quick pass has a smaller schema, so its prompt needs this extra guidance to
            # reach Haiku's 4,096-token minimum for prompt caching (and it improves accuracy).
            *([{"type": "text", "text": TRIAGE_GUIDE}] if mode != "full" else []),
            *([{"type": "text", "text": GATE_GUIDE}] if mode == "gate" else []),
            # Stable per user, so it sits before the cache breakpoint with the instructions.
            {"type": "text", "text": user_config_block(user), "cache_control": {"type": "ephemeral"}},
        ],
        messages=[{
            "role": "user",
            "content": (f"Today's date: {today}\nPost date: {posted_at or 'unknown'}\n\n"
                        f"<post>\n{post_text}\n</post>" + instruction),
        }],
        output_config={"format": {"type": "json_schema", "schema": schema}},
    )
    u = response.usage
    usage = {
        "input_tokens": u.input_tokens,
        "output_tokens": u.output_tokens,
        "cache_read_tokens": u.cache_read_input_tokens or 0,
        "cache_write_tokens": u.cache_creation_input_tokens or 0,
    }
    cost = cost_usd(model, usage)
    if response.stop_reason != "end_turn":
        raise AnalysisError(f"model stopped with stop_reason={response.stop_reason}", usage, cost)
    raw_text = next(b.text for b in response.content if b.type == "text")
    parsed = json.loads(raw_text)
    if mode == "gate":
        return parsed, raw_text, usage, cost
    if mode == "triage":
        parsed["facts"] = partial_to_full(parsed["facts"])
    parsed = _blank_to_none(parsed)
    parsed["facts"] = normalize_facts(parsed["facts"])
    if mode == "triage":
        parsed["facts"]["_partial"] = True   # only the quick-pass facts are real; the rest are blanks
    return parsed, raw_text, usage, cost


def _blank_to_none(v):
    """Recursively replace "" (the schema's "unknown text") with None."""
    if isinstance(v, dict):
        return {k: _blank_to_none(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_blank_to_none(x) for x in v]
    return None if v == "" else v


_ZERO_IS_UNKNOWN = ("rent_ils", "rooms", "size_sqm", "building_floors", "lease_months")


def normalize_facts(f):
    """Turn schema sentinels into None and compute monthly totals deterministically."""
    f = dict(f)
    for k in _ZERO_IS_UNKNOWN:
        if f[k] == 0:
            f[k] = None
    try:
        f["floor"] = int(f["floor"]) if f["floor"] is not None else None
    except ValueError:
        f["floor"] = None
    rm = f["roommates"] = dict(f["roommates"])
    for k in ("age_min", "age_max"):
        if rm[k] == 0:
            rm[k] = None
    if rm["current_roommate_count"] == -1:
        rm["current_roommate_count"] = None
    costs = f["additional_costs"] = [dict(c, amount_ils=c["amount_ils"] or None) for c in f["additional_costs"]]

    # Extras that are paid on top of rent. "total known" is a lower bound when some
    # extra is mentioned without an amount (extras_with_unknown_amount > 0).
    extra = [c for c in costs if c["included_in_rent"] != "yes"]
    known = [c["amount_ils"] for c in extra if c["amount_ils"] is not None]
    f["additional_monthly_ils"] = sum(known) if known else None
    f["extras_with_unknown_amount"] = sum(1 for c in extra if c["amount_ils"] is None)
    f["total_monthly_ils_known"] = None if f["rent_ils"] is None else f["rent_ils"] + sum(known)
    return f


# --- quick first pass ("stop at the first dealbreaker") ---------------------------------
# Just enough facts for the code checks in hard_rules.py to verify a rejection, plus the
# decision. Everything else is only extracted if the post survives (mode="full").
_TRIAGE_FACT_KEYS = ["is_rental_offer", "listing_type", "rent_ils", "rent_scope", "additional_costs",
                     "city", "location_summary_en", "roommates"]
TRIAGE_SCHEMA = _obj({
    "facts": _obj({k: FACTS_SCHEMA["properties"][k] for k in _TRIAGE_FACT_KEYS}),
    "decision": SCHEMA["properties"]["decision"],
})

TRIAGE_INSTRUCTION = """

MODE: QUICK FIRST PASS. Fill in only the fields of the schema; extract nothing else. Apply the \
decision rules above: REJECT only on explicit evidence that the post violates a hard requirement \
of the configuration; otherwise PASS, or NEEDS_REVIEW when unclear. Keep decision.reason to one \
short sentence."""


def _default_for(schema):
    """Blank value for a schema node: "" / "unknown" / 0 / [] - the 'not extracted' sentinels."""
    t = schema["type"]
    if isinstance(t, list):
        t = [x for x in t if x != "null"][0]
    if t == "object":
        return {k: _default_for(v) for k, v in schema["properties"].items()}
    if t == "array":
        return []
    if t == "string":
        enum = schema.get("enum")
        if enum:
            return "unknown" if "unknown" in enum else ("not_stated" if "not_stated" in enum else enum[0])
        return ""
    return False if t == "boolean" else 0


def partial_to_full(partial):
    """Quick-pass facts -> a complete facts dict, with blanks for everything not extracted."""
    full = _default_for(FACTS_SCHEMA)
    full.update(partial)
    full["roommates"]["current_roommate_count"] = full["roommates"].get("current_roommate_count", -1)
    full["extraction_confidence"] = "low"
    return full

TRIAGE_GUIDE = """\
## Quick-pass guide (applies to this first pass only)

The first pass rejects clear mismatches cheaply and lets everything else through to a full \
analysis. When unsure, do NOT reject: return NEEDS_REVIEW so the post gets the full analysis.

Typical posts and how to treat them:
- Someone searching for a place ("מחפש/ת דירה", "מחפשים חדר", "מישהו מכיר דירה?", "דרושה דירה") \
is not a rental offer: is_rental_offer false, decision REJECT, rule not_a_rental_offer.
- Property for sale ("למכירה", "מחיר שיווק", "דירה 4 חדרים ב-2,350,000 ₪", a developer's project or \
an agent's sales ad) is not a rental offer. Neither are services, furniture, cars, events or \
giveaways.
- Short-term stays - Airbnb, vacation rentals, hotel-style rooms priced per night ("ללילה", \
"סוף שבוע") - are not long-term rentals: listing_type "other" (or "sublet" when an ordinary flat \
is handed over temporarily), then apply the allowed_types rule.
- "מתפנה חדר", "מחפשים שותף/ה", "חדר בדירת שותפים" = roommate_search. "עוזבת ומחפשת מחליפה", \
"העברת חוזה", "כניסה במקומי" = room_replacement. "סאבלט", "לחודשיים", "לתקופה קצרה" = sublet. \
"דירת 3 חדרים להשכרה" with no roommates = whole_apartment. A whole house or villa ("בית פרטי", \
"קוטג'", "וילה") is listing_type "other".
- Price: "3,300 + 700 ועד וחשבונות" means rent 3300 plus one extra cost of 700 (bills_bundle). \
"כולל" means the named costs are included. "30k" means 30,000. A price for a whole apartment is \
rent_scope whole_apartment even when the post says it suits roommates ("קלסית ל-3 שותפים"); a \
price for one room is single_room_or_share.
- Gender: "מחפשות שותפה", "רק בנות", "לשותפה בלבד", "מחפשים מישהי" = a strict requirement for a \
woman. "עדיפות לבת", "עדיף בחורה" = only a preference: do not reject on it. "שותף/ה", \
"בנים ובנות" = no requirement.
- Age: "בני/בנות 30+", "בטווח 25-30", "מעל 28" are strict when stated as a requirement; \
"סביב גיל 30", "עדיפות לגילאי 25-30" are preferences.
- Location: a neighbourhood, street, landmark or walking time is location information even \
without a city name (פלורנטין, נווה צדק, כרם התימנים, הצפון הישן, יפו, דיזנגוף are in Tel Aviv; \
רמת חן, קריית קריניצי are in Ramat Gan). "10 דקות מעזריאלי" is a location hint. If the city \
cannot be determined, leave city blank and do not reject on location.
- Evidence: every rejection other than not_a_rental_offer needs a verbatim quote from the post in \
the relevant evidence field (gender_evidence, age_evidence); if you cannot quote it, return \
NEEDS_REVIEW instead of REJECT.
- Missing information (no price, no location, no date) is never a reason to reject.
- Fill the schema honestly and briefly; unknown values use the sentinels described above.
"""

# --- gate: the cheapest first question -------------------------------------------------
# Most posts are rejected for one of two reasons: they are not a rental offer, or they are
# the wrong listing type. The gate asks only that (about 25 output tokens); the CODE then
# applies the configured rules to the answer. Anything uncertain moves on to the next stage.
GATE_SCHEMA = _obj({
    "is_rental_offer": {"type": "boolean"},
    "listing_type": _enum("whole_apartment", "roommate_search", "room_replacement", "sublet", "other"),
    "confidence": _enum("high", "low"),
})

GATE_INSTRUCTION = """

MODE: GATE. Answer only the three schema fields, nothing else. is_rental_offer: does the post \
offer a place to rent (see the guide above)? listing_type: whole_apartment, roommate_search, \
room_replacement, sublet, or other (a whole house or villa, a short-term/vacation stay, or anything \
else). confidence: "high" when the post makes both answers clear, even if it looks unusual; "low" \
only when it is genuinely ambiguous, such as the cases listed in the guide."""

GATE_GUIDE = """\
## Gate guide: labelled examples (applies to the gate question only)

Each line shows a typical post and the answer: is_rental_offer / listing_type.

Offers of a place to rent (is_rental_offer true):
- "להשכרה דירת 3 חדרים ברחוב דיזנגוף, 6,500 ש"ח, כניסה מיידית" -> true / whole_apartment
- "להשכרה דירת 2 חדרים משופצת, קומה 3, ללא תיווך" -> true / whole_apartment
- "דירת גן להשכרה בפלורנטין, 4 חדרים" -> true / whole_apartment
- "מתפנה חדר בדירת שותפים בנווה צדק, 3,200 כולל" -> true / roommate_search
- "מחפשים שותפה לדירה בצפון הישן, אנחנו שתיים" -> true / roommate_search
- "חדר פנוי בדירה של 3, מחפשים שותף נקי ושקט" -> true / roommate_search
- "חדר בדירת שותפים ליד האוניברסיטה, 2,890 + 590 חשבונות" -> true / roommate_search
- "עוזבת את הדירה בסוף החודש ומחפשת מחליפה לחוזה" -> true / room_replacement
- "מעבירה חוזה על חדר ברחוב הרצל, כניסה ב-1.11" -> true / room_replacement
- "מחליף/ה לחדר שלי, העברת חוזה, הבעלים מסכים" -> true / room_replacement
- "סאבלט ל-3 חודשים, חדר מרוהט במרכז העיר" -> true / sublet
- "מסבלט את הדירה לחודשיים, מ-1.12 עד 31.1" -> true / sublet
- "להשכרה בית פרטי 6 חדרים עם גינה" -> true / other
- "וילה להשכרה ברמת גן, 7 חדרים" -> true / other
- "יחידת דיור / סטודיו קטן בחצר, להשכרה" -> true / other (not a regular apartment)
- A post that offers a roommate search AND mentions the price of the whole flat: still roommate_search.

Not offers (is_rental_offer false):
- "מחפשת דירה במרכז העיר, 2-2.5 חדרים עד 6000" -> false (a person searching)
- "מחפש חדר בתל אביב, תקציב עד 3,500" -> false
- "דרושה דירה בגבעתיים לזוג" -> false
- "מישהו מכיר דירה פנויה באזור? תודה" -> false
- "אנחנו שלושה חברים ומחפשים דירה לשכור יחד" -> false (no flat yet)
- "יש לי לקוחות שמחפשים דירה, מי שיש לו שיפנה אליי" -> false (a broker's search request)
- "למכירה דירת 4 חדרים, מחיר שיווק 2,340,000" -> false (a sale)
- "מכירת דירה בפרויקט חדש, מעורבות קבלן" -> false
- "PENTHOUSE AIRBNB IN TEL AVIV, per night" -> false (short vacation stay)
- "דירת נופש ליום / לסוף שבוע" -> false
- "למסירה ספה ושולחן, בחינם" -> false (not housing)
- "שירותי הובלות והתקנות, מחירים הוגנים" -> false
- "מה דעתכם על השכונה? המלצה על מתווך?" -> false (a question)
- "פרטים בפרטי" alone, a photo with no text, or a single link -> false, confidence low

Low confidence ("low") examples: a post listing several different apartments at once; very short \
posts that only say "זמין", "פרטי" or "מחיר בפרטי" with no description; text that is mostly in \
another language and unclear; a post that could be either a roommate search or a replacement of \
a leaving tenant; an ad for a room in a hotel-like or hostel setting. Use "low" only for genuinely \
ambiguous posts like these - a clear post gets "high" even when it is not the kind of listing \
anyone wants, since a later stage and the code rules handle the decision.
"""