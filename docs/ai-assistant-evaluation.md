# FixNow AI assistant evaluation

## Supported behavior

The assistant can interpret supported home-service requests, resolve provider
coverage by database-backed PIN or area, show eligible providers, carry bounded
conversation context, and hand an explicitly selected provider to FixNow's
existing booking form. It can also handle safe clarification, cancellation of
the current conversational action, and deterministic emergency guidance.

Provider, category, service-area, approval, and availability facts are decided
by Django queries. Ollama is used only for natural-language normalization; its
output is schema-validated and cannot create provider or booking facts.

## Booking path

The assistant does not create bookings directly. After a current discovery
result, a request such as “book Ramesh” must select a provider from that result,
revalidate the provider against current database eligibility, and return a
same-origin link to `/book/<provider_id>/`. The existing authenticated booking
form validates the date, time, address, PIN coverage, and description before
creating `Booking`, `BookingStatusLog`, and `Notification` records. Repeated
identical submissions reuse the existing pending/confirmed booking.

## Safety and limitations

Hazard detection runs before Ollama and prevents booking during emergency
guidance. Model responses, client history, provider names, and action context
are untrusted. Browser-carried context is bounded and server-revalidated.

The assistant does not provide live appointment availability, prices, ratings
as recommendations, payments, provider messaging, or unsupported cancellation
and rescheduling workflows. Existing cancellation remains available through
the authenticated customer booking pages.

## Verification

Run the focused tests with:

```text
./.venv/bin/python manage.py test ai_assistant.test_provider_discovery fixnow_app.tests_booking
```

Run the complete AI suite with:

```text
./.venv/bin/python manage.py test ai_assistant
```

Also run `./.venv/bin/python manage.py check` and `git diff --check`.

Milestone verification on 2026-10-20:

- Focused provider-discovery and booking tests: 25 passed.
- Complete `ai_assistant` suite: 73 passed.
- Django system checks: passed.
- JavaScript syntax check (`node --check static/js/fixnow_chat.js`): passed.
- `git diff --check`: passed.

The test run uses Django's isolated temporary test database. It does not alter
the local development database.

## Model providers and controlled tools

The assistant has an explicit `AI_PROVIDER` setting. `ollama` is the local
default and uses `OLLAMA_BASE_URL` and `OLLAMA_MODEL`. Hosted demonstrations can
set `AI_PROVIDER=gemini`, `GEMINI_MODEL`, and `GEMINI_API_KEY`; the key is read
server-side and is never sent to the browser. There is no automatic fallback
between providers. Gemini uses the official `generateContent` function
declaration format; Ollama receives an explicit tool list, but model support
must be verified with the configured model because not every Ollama model
supports tools.

Only `search_providers` and `get_provider_details` are exposed. Both tools
validate arguments, query the existing Django discovery functions, cap result
size, and revalidate current provider eligibility. Tool calls are bounded to
three rounds and cannot execute model-generated Python, SQL, shell commands, or
arbitrary database queries.

Evaluation cases include paraphrased service requests, missing location
clarification, database-only provider names, stale provider revalidation,
unknown-tool rejection, malformed arguments, prompt-injection attempts, and
safe booking handoff. Automated provider tests mock upstream model responses;
no live Gemini credential is required.

For a local demonstration, run Ollama with a model that documents tool support,
then set `AI_PROVIDER=ollama`, `OLLAMA_BASE_URL`, and `OLLAMA_MODEL`. For a
hosted demonstration, set `AI_PROVIDER=gemini`, `GEMINI_MODEL`, and
`GEMINI_API_KEY` in the deployment environment only. Do not put a real key in
`.env.example` or frontend code.
