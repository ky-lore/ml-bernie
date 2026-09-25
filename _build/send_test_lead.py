"""Send the GHL mapping test request for one form.

GHL builds its field-mapping list from the first request a new Inbound Webhook
trigger receives, so this sends EVERY key the live form will send, filled with
realistic sample values, in exactly the shape server.py relays.

Usage: python3 _build/send_test_lead.py <slug> <GHL inbound webhook URL>
   slugs: quote | contact
"""
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from server import FORMS, GHL_URL_SHAPE, LEAD_KEYS, clean  # noqa: E402

POSITIONS = {"quote": "hero", "contact": "contact"}

if len(sys.argv) != 3 or sys.argv[1] not in FORMS:
    sys.exit(__doc__)
slug, url = sys.argv[1], sys.argv[2].strip()
if not GHL_URL_SHAPE.match(url):
    sys.exit("That doesn't look like a GHL Inbound Webhook trigger URL "
             "(https://services.leadconnectorhq.com/hooks/<locationId>/webhook-trigger/<triggerId>). Check it before sending.")

sample = {
    "first_name": "Test", "last_name": "Lead", "email": "test.lead@example.com",
    "phone": "(310) 555-0123", "business_name": "Test Tire Shop",
    "customer_type": "Tire shop",
    "message": f"Test submission from AM webhook setup ({slug}): 2 cases of valve stems",
    "page_url": "https://mlbernie.com/",
    "form_position": POSITIONS[slug],
    "gclid": "test-gclid", "gbraid": "test-gbraid", "wbraid": "test-wbraid", "fbclid": "test-fbclid",
    "utm_source": "google", "utm_medium": "cpc", "utm_campaign": "test-campaign",
    "utm_term": "tire shop supplies gardena", "utm_content": "test-ad",
}
payload = clean(sample, slug)
assert list(payload) == LEAD_KEYS
print(json.dumps(payload, indent=2))
req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
with urllib.request.urlopen(req, timeout=15) as r:
    print("GHL responded", r.status, r.read()[:200].decode(errors="replace"))
print(f"\nNow open workflow 'ENDPOINT - {slug}' in GHL, fetch the sample request in the trigger, confirm the payload, and save.")
