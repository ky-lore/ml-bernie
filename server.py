"""M. L. Bernie Co. web server: static site + lead relay to GoHighLevel.

Serves the one-page site and exposes POST /api/leads/<slug>. Each form slug
relays to its own GHL Inbound Webhook workflow ("ENDPOINT - <slug>"), whose URL
lives in a Railway variable so it never reaches the browser:

    quote    -> GHL_WEBHOOK_URL_QUOTE     (hero "Get a Quote" form, form_position = "hero")
    contact  -> GHL_WEBHOOK_URL_CONTACT   (contact section "Request a Quote" form,
                                           form_position = "contact")

Payload sent to GHL is flat snake_case JSON with the same keys for every form
(see LEAD_KEYS), so every workflow maps identically. Standard library only.

Run: PORT=8080 python3 server.py
"""
import json
import mimetypes
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict, deque
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
# Slim images (python:alpine) ship without /etc/mime.types
for _ext, _type in {".webp": "image/webp", ".svg": "image/svg+xml", ".js": "application/javascript", ".txt": "text/plain",
                    ".webm": "video/webm", ".mp4": "video/mp4", ".avif": "image/avif"}.items():
    mimetypes.add_type(_type, _ext)
PORT = int(os.environ.get("PORT", "8080"))

FORMS = {
    "quote": "GHL_WEBHOOK_URL_QUOTE",
    "contact": "GHL_WEBHOOK_URL_CONTACT",
}

# Every key GHL receives, in order. Send all of them on every request (empty
# string when not collected) so the mapping built from the test request holds.
LEAD_KEYS = [
    "first_name", "last_name", "full_name", "email", "phone", "business_name",
    "customer_type", "message", "form_summary",
    "source", "form_name", "form_position", "page_url", "submitted_at",
    "gclid", "gbraid", "wbraid", "fbclid",
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
]
FREE_TEXT = {"message": 2000}
YES_NO = set()
SUMMARY_LABELS = [
    ("business_name", "Business"),
    ("customer_type", "Customer type"),
    ("message", "What they need"),
    ("form_position", "Form position"),
    ("page_url", "Page"),
]

GHL_URL_SHAPE = re.compile(r"^https://services\.leadconnectorhq\.com/hooks/[A-Za-z0-9]+/webhook-trigger/[A-Za-z0-9-]+$")

# Only site files are public: pages and media at the root plus the PRODUCTS/ photo
# library. server.py, Dockerfile, *.md (onboarding notes), _build/ etc. are never served.
PUBLIC_DIRS = ("PRODUCTS/",)
PUBLIC_EXT = (".html", ".jpg", ".jpeg", ".png", ".webp", ".avif", ".svg", ".mp4", ".webm", ".ico", ".txt", ".xml")
REDIRECTS = {}

NOT_FOUND = ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
             "<title>Page not found — M. L. Bernie Co.</title><body style='margin:0;min-height:100vh;display:grid;place-items:center;"
             "background:#0c0c0d;color:#e7e6e2;font:17px/1.6 system-ui,sans-serif;text-align:center;padding:16px'><div>"
             "<h1 style='font:700 32px system-ui;text-transform:uppercase;color:#fff'>Page not found</h1>"
             "<p style='margin:12px 0 24px'>Call <a style='color:#c99a3b' href='tel:3109656422'>(310) 965-6422</a> or head back to the site.</p>"
             "<a style='background:#c99a3b;color:#1a1204;padding:12px 22px;text-decoration:none;font-weight:600' href='/'>Back to M. L. Bernie Co.</a>"
             "</div></body>")

RATE_LIMIT = 6          # submissions
RATE_WINDOW = 600       # seconds, per IP
MIN_FILL_MS = 2500      # faster than this is a bot
_hits = defaultdict(deque)


def log(*a):
    print(datetime.now().isoformat(timespec="seconds"), *a, file=sys.stderr, flush=True)


def e164(raw):
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return raw.strip() if raw else ""


def clean(body, slug):
    s = lambda k, n=200: str(body.get(k, "") or "").strip()[: FREE_TEXT.get(k, n)]
    lead = {k: s(k) for k in LEAD_KEYS}
    lead["email"] = lead["email"].lower()
    lead["phone"] = e164(lead["phone"])
    if not lead["last_name"] and " " in lead["first_name"]:
        lead["first_name"], lead["last_name"] = lead["first_name"].split(" ", 1)
    lead["full_name"] = " ".join(p for p in (lead["first_name"], lead["last_name"]) if p)
    for k in YES_NO:
        lead[k] = "Yes" if lead[k].lower() in ("yes", "true", "on", "1") else "No"
    lead["source"] = "website"
    lead["form_name"] = slug
    lead["page_url"] = s("page_url", 500)
    lead["submitted_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lead["form_summary"] = "\n".join(f"{label}: {lead[k]}" for k, label in SUMMARY_LABELS if lead[k])
    return lead


def rate_limited(ip):
    now = time.time()
    q = _hits[ip]
    while q and now - q[0] > RATE_WINDOW:
        q.popleft()
    if len(q) >= RATE_LIMIT:
        return True
    q.append(now)
    return False


class Handler(SimpleHTTPRequestHandler):
    server_version = "MLB"
    sys_version = ""

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def log_message(self, fmt, *args):
        if os.environ.get("ACCESS_LOG"):
            log(self.address_string(), fmt % args)

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        path = self.path.split("?", 1)[0]
        if path.startswith("/PRODUCTS/") or path.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".avif", ".mp4")):
            self.send_header("Cache-Control", "public, max-age=604800")
        super().end_headers()

    # ---------------------------------------------------------------- static
    def _public_path(self):
        path = urllib.parse.unquote(self.path.split("?", 1)[0].split("#", 1)[0])
        rel = path.strip("/")
        if rel == "":
            return "index.html"
        if ".." in rel or rel.startswith(".") or "/." in rel:
            return None
        if "/" not in rel:
            if rel.lower().endswith(PUBLIC_EXT):
                return rel
            if os.path.isfile(os.path.join(ROOT, rel + ".html")):
                return rel + ".html"      # clean URLs: /thank-you -> thank-you.html
            return None
        return rel if rel.startswith(PUBLIC_DIRS) else None

    def _send_file(self, rel, status=200, head=False):
        full = os.path.join(ROOT, rel)
        with open(full, "rb") as f:
            data = f.read()
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "image/svg+xml"):
            ctype += "; charset=utf-8"
        # Byte ranges: Safari/iOS won't play <video> without 206 responses
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", "").strip()) if status == 200 else None
        if m and (m.group(1) or m.group(2)):
            size = len(data)
            if m.group(1):
                start, end = int(m.group(1)), int(m.group(2) or size - 1)
            else:
                start, end = max(0, size - int(m.group(2))), size - 1
            end = min(end, size - 1)
            if start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                return self.end_headers()
            data = data[start:end + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(status)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if not head:
            self.wfile.write(data)

    def _static(self, head=False):
        old = self.path.split("?", 1)[0].strip("/")
        if old in REDIRECTS:
            self.send_response(301)
            self.send_header("Location", REDIRECTS[old])
            self.send_header("Content-Length", "0")
            return self.end_headers()
        rel = self._public_path()
        if rel and os.path.isfile(os.path.join(ROOT, rel)):
            return self._send_file(rel, head=head)
        data = NOT_FOUND.encode()
        self.send_response(404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if not head:
            self.wfile.write(data)

    def do_GET(self):
        self._static()

    def do_HEAD(self):
        self._static(head=True)

    # ---------------------------------------------------------------- leads
    def _json(self, status, obj):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        m = re.fullmatch(r"/api/leads/([a-z0-9-]+)", self.path.split("?", 1)[0])
        if not m or m.group(1) not in FORMS:
            return self._json(404, {"ok": False, "error": "unknown form"})
        slug = m.group(1)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 20000:
                return self._json(413, {"ok": False})
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return self._json(400, {"ok": False, "error": "bad json"})

        # spam: honeypot + too-fast submissions get a fake success
        try:
            elapsed = int(body.get("elapsed_ms") or 0)
        except (TypeError, ValueError):
            elapsed = 0
        if body.get("company_website") or elapsed < MIN_FILL_MS:
            log("lead dropped as bot", slug)
            return self._json(200, {"ok": True})
        ip = (self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip()
        if rate_limited(ip):
            return self._json(429, {"ok": False, "error": "too many requests"})

        lead = clean(body, slug)
        if not lead["first_name"] or len(re.sub(r"\D", "", lead["phone"])) < 10:
            return self._json(400, {"ok": False, "error": "name and phone are required"})

        url = os.environ.get(FORMS[slug], "").strip()
        if not url:
            log(f"lead NOT delivered: {FORMS[slug]} is not set", slug, lead["phone"])
            return self._json(503, {"ok": False, "error": "form not connected"})
        req = urllib.request.Request(url, data=json.dumps(lead).encode(), headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                if r.status >= 300:
                    raise urllib.error.HTTPError(url, r.status, "bad status", r.headers, None)
        except (urllib.error.URLError, TimeoutError) as err:
            log("GHL webhook failed", slug, getattr(err, "code", ""), err)
            return self._json(502, {"ok": False, "error": "delivery failed"})
        log("lead delivered", slug)
        return self._json(200, {"ok": True})


if __name__ == "__main__":
    for slug, var in FORMS.items():
        url = os.environ.get(var, "").strip()
        if not url:
            log(f"WARNING {var} not set: '{slug}' form will ask visitors to call")
        elif not GHL_URL_SHAPE.match(url):
            log(f"WARNING {var} doesn't look like a GHL inbound webhook trigger URL")
    log(f"serving {ROOT} on :{PORT}")
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()
