# Revolut Payment Gateway

Accept payments in Odoo via the Revolut Merchant API hosted checkout page — debit/credit
cards and Revolut Pay (Apple Pay / Google Pay / Pay by bank surface automatically on the
hosted page where available).

Built and maintained by Fusion Technologies, an official Odoo partner.

## How it works

1. The customer picks Revolut at checkout; Odoo creates a Revolut order server-side and
   redirects to the hosted checkout page (smallest possible PCI surface — SAQ-A).
2. After payment, the customer is redirected back; Odoo fetches the authoritative order
   state from the API (the redirect alone is never trusted).
3. Webhooks are the source of truth for the final state: signed notifications
   (HMAC-SHA256, timestamp tolerance, secret-rotation aware) complete the transaction
   even if the customer never returns to the shop.

Supports: eCommerce checkout, invoice portal payments, full and partial refunds from the
transaction form, manual capture/void (enable "Capture Amount Manually" on the provider),
multi-currency (21 currencies incl. EUR, GBP, USD).

## Setup

1. **Get your API key**: Revolut Business → Merchant API → generate the *secret* API key
   (production), or create a sandbox account at sandbox-business.revolut.com for testing.
2. In Odoo: *Invoicing → Configuration → Payment Providers → Revolut*, paste the secret
   key, set the state (*Test Mode* uses the sandbox API; *Enabled* uses production —
   each environment has its own key).
3. Click **Register Webhook**. This creates the webhook on Revolut for your instance's
   base URL and stores the signing secret automatically. The webhook URL must be
   reachable from the internet (use a tunnel for local development; the URL cannot be
   localhost or a bare IP).
4. Enable the provider and publish it.

Sandbox test cards are listed in the Revolut docs (e.g. `4929420573595709` succeeds;
`4929573638125985` declines with insufficient_funds).

## Technical notes

- Pinned `Revolut-Api-Version: 2026-03-12` (see `const.py`, single source of truth for
  endpoints, status maps and event lists).
- The webhook handler is idempotent and state-machine guarded; events arriving out of
  order or replayed are safe.
- Secrets are restricted to *Settings* users (`base.group_system`), masked in logs, and
  reset by database neutralization.
