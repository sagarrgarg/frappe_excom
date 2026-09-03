# Excom

Omnichannel communication platform for Frappe/ERPNext. Brings WhatsApp, Email, Instagram DMs and Messenger into a single real-time inbox with unified contact identity, team assignment, and full ERP context.

## What It Does

- **Unified Inbox** — All channels in one place. WhatsApp, Email (via Gmail API), Instagram DMs and Facebook Messenger (Graph API, polled every minute), with a single conversation timeline per contact.
- **Omni Identity** — Automatically links phone, email, and WhatsApp to one contact profile tied to ERPNext Customer/Lead/Supplier.
- **Team Assignment** — Route conversations to teams, transfer between agents, claim from the general queue.
- **Broadcast Messaging** — Send bulk WhatsApp templates and emails to subscriber lists with delivery tracking.
- **ERP Context** — See linked quotations, sales orders, invoices, and purchase documents inline while chatting.
- **Email Signatures, Canned Responses, Tags, Stickers** — Productivity tools for agents built in.

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.10+, Frappe Framework, ERPNext |
| Frontend | React 18, TypeScript, Tailwind CSS, frappe-react-sdk |
| Database | MariaDB (via Frappe ORM) |
| Realtime | Frappe Socket.IO |
| External APIs | Gmail API (OAuth2), WhatsApp Cloud API, Meta Graph API (Instagram / Messenger / Lead Ads) |

## Installation

Requires [Frappe Bench](https://github.com/frappe/bench) v5.x+ and **yarn** (for frontend builds).

```bash
cd $PATH_TO_YOUR_BENCH

bench get-app https://github.com/sagarrgarg/frappe_excom.git
bench install-app excom
bench migrate
```

### Frontend Build

The React frontend requires a separate build step after installation or any frontend change:

```bash
cd apps/excom/frontend
yarn install
yarn build
```

Or use bench to build all app assets at once:

```bash
bench build --app excom
```

## Development

```bash
bench start                                      # Start dev server
bench build --app excom                          # Build frontend
bench migrate                                    # Apply DocType schema changes
bench run-tests --app excom                      # Run all tests
bench run-tests --app excom --module excom.tests.test_<name>  # Single module
bench console                                    # Python REPL with Frappe context
```

### Configuration

After installation, configure your channel accounts in the Excom settings:

1. **WhatsApp** — Add your WhatsApp Business API credentials (Phone Number ID, Business ID, Access Token, App Secret for webhook HMAC validation)
2. **Email** — Connect Gmail accounts via OAuth2 (Connected App setup)
3. **Instagram / Messenger** — Graph API polling (`channels/meta_dm/service.py`); webhook `messaging[]` as accelerator; 24h reply window, HUMAN_AGENT tag optional

Access the inbox at `https://your-site.com/excom`.

## Project Structure

```
excom/excom/
├── api/           # @frappe.whitelist() REST endpoints
├── channels/      # Channel adapters (email/, whatsapp/)
├── doctype/       # Frappe DocTypes (Thread, Message, Channel Account, etc.)
├── services/      # Business logic layer
├── tasks/         # Scheduled background jobs
├── utils/         # Shared utilities

frontend/src/
├── components/    # React components
├── hooks/         # Custom React hooks (API calls, realtime, state)
├── types/         # TypeScript interfaces
├── utils/         # Frontend utilities
```

## Issues

Found a bug or have a feature request? Open an issue:

https://github.com/sagarrgarg/frappe_excom/issues

## License

MIT
