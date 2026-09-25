# GHL Webhooks — M. L. Bernie Co.

Each website form sends leads into the client's GHL sub-account through its own **Inbound Webhook** workflow (AM Webhooks → GHL process).

```
browser form ──POST JSON──▶ /api/leads/<slug>  (server.py on Railway)
                              │  validates, cleans (E.164 phone, lowercase email, full_name),
                              │  builds form_summary, honeypot + speed check + rate limit
                              ▼
                        env GHL_WEBHOOK_URL_<SLUG> ──▶ GHL workflow "ENDPOINT - <slug>"
```

Webhook URLs live **only** in Railway variables (project `SITE : ML Bernie`, service `ml-bernie`) and never reach the browser. If a variable is missing, that form tells the visitor to call (310) 965-6422 and the server logs `lead NOT delivered`.

## Forms (one endpoint each)

| Form | Where | Slug / `form_name` | GHL workflow | Railway variable |
|---|---|---|---|---|
| Get a Quote (name, phone, I'm a…, what do you need) | Hero, top of page | `quote` | `ENDPOINT - quote` | `GHL_WEBHOOK_URL_QUOTE` |
| Request a Quote (name, business, phone, email, I'm a…, what do you need) | Contact section, bottom of page | `contact` | `ENDPOINT - contact` | `GHL_WEBHOOK_URL_CONTACT` |

## Payload (identical keys for every form)

Every request sends **all** keys (empty string when a form doesn't collect it), so both workflows map the same way.

| JSON key | GHL field | Notes |
|---|---|---|
| `first_name` | First Name | required; a full name typed in one box is split by the server |
| `last_name` | Last Name | |
| `full_name` | Full Name | built by the server |
| `email` | Email | lowercased; contact form only |
| `phone` | Phone | required, E.164 (`+13105550123`) |
| `business_name` | Company Name | contact form only |
| `customer_type` | Custom field **Customer Type** | Tire shop / Reseller / distributor / Mobile road service / Other |
| `message` | Custom field **Lead Message** (large text) | "What do you need?" |
| `form_summary` | Custom field **Form Summary** (large text) | all answers as readable lines — map only this if you want one field |
| `source` | Source | always `website` |
| `form_name` | Custom field **Form Name** | `quote` / `contact` |
| `form_position` | Custom field **Form Position** | `hero` / `contact` |
| `page_url` | Custom field **Page URL** | |
| `submitted_at` | Custom field **Submitted At** | UTC ISO timestamp |
| `gclid`, `gbraid`, `wbraid` | Custom fields / GHL attribution | Google Ads click IDs |
| `fbclid` | Custom field | Meta click ID |
| `utm_source` … `utm_content` | Custom fields | |

Attribution is kept for the whole visit; a new tagged landing replaces the whole set so two ad clicks never mix.

## Setup steps (per form)

1. GHL → Automation → Workflows → **Create workflow → Start from scratch**, name it `ENDPOINT - <slug>`.
2. Add trigger **Inbound Webhook**, copy its URL.
3. Send the mapping test: `python3 _build/send_test_lead.py <slug> <webhook URL>`
4. In the trigger, **Fetch sample request**, confirm the payload, save the trigger.
5. Add a **Create/Update Contact** action mapping the keys above; then tags (`web-lead`, `form:<slug>`), an internal alert, and an opportunity if they use a pipeline. **Publish** the workflow.
6. Set the URL in Railway as `GHL_WEBHOOK_URL_<SLUG>` and redeploy.

## Spam and tracking

- Hidden honeypot field + submissions faster than 2.5s get a fake success and are dropped.
- 6 submissions per IP per 10 minutes.
- The browser waits ~1.5s before posting (GHL can drop instant posts), waits for the reply, pushes `generate_lead` to the dataLayer, then goes to `/thank-you`.

## Open items

- No SMS consent checkbox yet: the site has no Privacy Policy / Terms pages to link for A2P. Don't send marketing SMS from these workflows until that's added.
- No GTM / Google Ads tag on the site yet, so `generate_lead` has nowhere to go until one is installed.
