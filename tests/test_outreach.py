"""WhatsApp message template and the contact button."""
from apartment_bot import outreach
from apartment_bot.contacts import contact_info


def test_template_drops_parts_with_unknown_values():
    t = "היי[ {name}], ראיתי את המודעה בקבוצה {group}[ ב{area}][ ב-{price} ₪]."
    assert outreach.render_template(t, {"name": "Dana", "group": "G", "area": "", "price": "5,000"}) == \
        "היי Dana, ראיתי את המודעה בקבוצה G ב-5,000 ₪."
    assert outreach.render_template(t, {"group": "G"}) == "היי, ראיתי את המודעה בקבוצה G."


def test_template_values_from_facts():
    post = {"author": "Dana Levi"}
    v = outreach.template_values(post, {"rent_ils": 5000, "neighborhood": "Florentin", "city": "TLV"}, "G")
    assert v == {"name": "Dana", "area": "Florentin", "street": "", "group": "G", "price": "5,000"}
    assert outreach.template_values({"author": None}, None, "G")["price"] == ""


def _contact(text, author_id="1"):
    return contact_info({"author": "x", "author_id": author_id, "text": text})


def test_whatsapp_from_mobile_number():
    a = outreach.contact_action(_contact("call 052-123-4567"), "שלום")
    assert a["kind"] == "whatsapp" and a["url"].startswith("https://wa.me/972521234567?text=")


def test_whatsapp_link_wins_and_landline_skipped():
    assert outreach.whatsapp_number(_contact("03-5551234 wa.me/972541112222")) == "972541112222"
    a = outreach.contact_action(_contact("03-5551234"), "hi")
    assert a["kind"] == "facebook"


def test_no_contact_at_all():
    assert outreach.contact_action(_contact("nothing", author_id=None), "hi")["kind"] == "none"
