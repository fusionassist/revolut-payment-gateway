# payment_revolut — project notes

Working dir layout:
- `payment_revolut/` — the module (deploy this to the addons path)
- `.odoo19-ref/` — sparse clone of odoo/odoo 19.0 (payment, payment_stripe,
  payment_razorpay, payment_xendit) for reference only; not part of the module
- `.docs-cache/` — Revolut Merchant API doc pages cached 2026-06-11 (verified live)

## Decisions (deviations from the original build plan)

1. **Target Odoo 19 first, not 18.** The plan's "Odoo 18 (matches our own instance)" was
   stale: FusionERP/IDI was upgraded to 19.0 Enterprise on 2026-05-20 (atlas:
   systems/fusion-erp.md). Dogfooding (G1) therefore requires 19. The 18 port comes
   after, alongside 17 — note the payment framework was heavily refactored in 19
   (`_apply_updates`/`_extract_reference`/`_extract_amount_data`/`_process` replace the
   18-era `*_notification_data` methods), so the 18 port is a real port, not a copy.
2. **API version pinned `2026-03-12`** — newest version with a documented changelog
   entry (2026-04-20 is accepted by the API but undocumented).
3. **Request field renames vs plan:** `merchant_order_ext_ref` is only a webhook payload
   key now; the create-order request field is `merchant_order_data.reference`. Webhook
   CRUD lives at `/api/webhooks` (the `/api/1.0/webhooks` set is deprecated); update is
   PATCH; signing timestamp header is in *milliseconds*.
4. **Order state is the source of truth** for tx state. `ORDER_PAYMENT_DECLINED` does
   not error the tx: the customer can retry on the hosted page; only order state
   `failed` is terminal (decline reason then captured in `state_message`).
5. **Manual capture shipped in v1** as `full_only` (plan had it v1.1) — endpoints were
   verified and the code is symmetrical with refunds.
6. **Webhook events registered:** ORDER_COMPLETED, ORDER_AUTHORISED, ORDER_CANCELLED,
   ORDER_FAILED, ORDER_PAYMENT_DECLINED, ORDER_PAYMENT_FAILED.

## Open questions from the plan — answers (docs-verified 2026-06-11)

1. Revolut Pay surfaces automatically on hosted checkout; no API flag controls methods.
2. Card currencies (21): AUD CAD CHF CZK DKK EUR GBP HKD HUF ILS JPY MXN NOK NZD PLN RON
   SEK SGD TRY USD ZAR. Amounts are ISO 4217 minor units (module uses the framework's
   CURRENCY_MINOR_UNITS map). Revolut Pay supports more; provider filter uses card list.
3. Webhook URL: HTTP or HTTPS, no localhost/bare-IP hosts, no documented port limits;
   max 10 webhooks per account; retries 3× at 10-min intervals. Source IPs to allowlist —
   prod: 35.246.21.235, 34.89.70.170; sandbox: 35.242.130.242, 35.242.162.241.
4. Branding/naming for the Apps Store listing — still Gerry's call (Phase 5).
5. Selling entity — still Gerry's call (doesn't block).

## Confirmed by Gerry (2026-06-12)

- Branding: company **Fusion Technologies**; app display name **"Revolut Payment
  Gateway API"** (manifest updated; technical name stays `payment_revolut`).
- **Odoo 18 port is committed** (build order: verify 19 on sandbox → port to 18 → 17
  optional). 18 uses the pre-refactor framework (`_get_tx_from_notification_data` /
  `_process_notification_data` / `_handle_notification_data`).
- Primary currency EUR; webhook endpoint will live on https://erp.interpos.ai.
- The existing Odoo→Revolut bank-transactions integration (`custom_bank_sync`, Business
  API, PEM keys) stays untouched — completely separate API and credentials.

## Apps Store packaging checklist (Phase 5) — upload at https://apps.odoo.com/apps/upload
Requirements verified live 2026-06-14 (apps.odoo.com vendor guidelines):
- [x] App NAME set to "Revolut Payment Gateway" (23 chars, confirmed 2026-06-14).
      Technical name stays payment_revolut.
- [x] manifest: price=119.0 EUR set (2026-06-14). Competitor intel: only 1 other Revolut
      payment provider on the store, priced €128.83 → we undercut at €119 (compliant with
      "lowest price on web"). Plan's €148–198 band revised down for competitiveness.
- [ ] manifest: 'support': support email; 'images': ['static/description/...screenshot.png'];
      optional 'live_test_url'.
- [ ] static/description/icon.png — replace placeholder with real 140x140 PNG (real PNG, not renamed).
- [ ] static/description/index.html — rich listing page (draft from README; Odoo has a template).
- [ ] Screenshots (6–10 annotated): provider config+credentials, Register Webhook, portal
      Pay Now, Revolut hosted page, successful payment status, refund wizard, transaction states.
- [ ] Optional: doc/index.rst (pure rst, auto-loaded as docs).
- [ ] Git repo for submission (zip also allowed) — home: fusionassist GitHub org (see atlas KEYS.md).
- [ ] License OPL-1 already set; ok depending on LGPL-3 core `payment`. Odoo takes 30% / author 70%.
- [ ] Trademark: name must read as an integration, not Revolut-official — "Revolut Payment
      Gateway" is fine; Gerry's legal sanity check before go-live (open Q4).

## Competitor analysis (the only other Revolut provider, 2026-06-14)
"Revolut Payment Provider" by **Mindphin** — €128.83, Odoo 18+19, OPL-1, no ratings.
URL: https://apps.odoo.com/apps/modules/19.0/md_revolut_payment_gateway (~439 LOC, minimal).
- No name collision: theirs is "...Provider", ours is "...Gateway".
- They cover BOTH 18 + 19 → reinforces our 18 port as competitively necessary.
- Their listing does NOT mention: refunds, manual capture, multi-currency, 3DS,
  tokenization, signature verification.
- They depend on website_sale + website + account + mail (forces eCommerce/website install).

OUR differentiators to LEAD the index.html listing with:
1. Price €119 (< €128.83) — lowest on the store.
2. Full + partial REFUNDS from the transaction (they don't list it).
3. Manual capture / authorise-then-capture (they don't list it).
4. 21-currency multi-currency incl. EUR/GBP/USD (they don't list it).
5. Security: HMAC-SHA256 webhook signature verification, timestamp tolerance,
   rotation-aware, idempotent — vs their bare "webhook support".
6. Lightweight: depends ONLY on `payment` — works for INVOICE-ONLY businesses (no forced
   website_sale/eCommerce). Big edge for service businesses.
7. Partner-built (Fusion Technologies, official Odoo partner), follows Odoo's own
   Stripe/Razorpay conventions; adversarially reviewed + tested.
8. 90-day free support (matches market/their offer).

## Listing design (decided 2026-06-15)
- Hero banner: Gerry provided a polished Odoo-styled hero (Odoo logo + "Developed by
  Fusion Technologies" badge, Revolut mark + "Payment Gateway", eCommerce/Sales/Invoicing
  tiles, real hosted-checkout screenshot, bottom feature bar). SUPERSEDES Claude's
  banner.svg draft (navy/teal — wrong palette).
- Palette = ODOO AUBERGINE: primary #714B67, secondary #875A7B, light #F0EEF1. Use for
  index.html accents + any secondary graphic.
- COPY FLAG: "PCI DSS compliant" on the hero should be reworded — Revolut (processor) is
  PCI DSS L1; our hosted-checkout flow keeps the merchant at PCI SAQ-A (card data never
  touches Odoo). Safer wording: "PCI-compliant hosted checkout — card data handled by
  Revolut". (Sell the SAQ-A benefit, don't imply the add-on is certified.)
- TRADEMARK: Revolut mark = nominative use (ok, like Odoo's Stripe/PayPal modules); the
  "Developed by Fusion Technologies" badge signals third-party. Gerry legal sanity-check
  before go-live (open Q4).
- Assets to drop into static/description/: banner.png (final hero), icon.png (the black
  "R Pay" mark would work). Then Claude wires into index.html + manifest 'images'.
- [x] index.html BUILT (2026-06-15, Odoo-aubergine, accurate copy, leads with our
  differentiators). References banner.png + screenshot-{providers,config,checkout,refund}.png.
- [x] manifest 'images': ['static/description/banner.png'] added.
- [x] Assets DONE via lovable.dev repo (github.com/fusionassist/revolut-payments-for-odoo):
  Gerry generated a React marketing site that already contains a polished hero + all
  screenshots REDACTED to a consistent "ACME Solutions" demo company. Copied into
  static/description/: banner.png (=revolut-app.png hero w/ corrected PCI copy), icon.png
  (=revolut-pay.png, small ~3KB - flag), 6 screenshots (providers/config/checkout/invoice/
  messages/refund). Rebuilt index.html as STATIC HTML (Odoo can't run the React app) using
  lovable's copy, Odoo aubergine, white bg. manifest support=hello@fusiontechnologies.ie.
- DECISIONS PENDING (flagged to Gerry 2026-06-15):
  - 18 vs 19: lovable site claims "Odoo 18 & 19" but only 19 is built/validated. Odoo
    index.html set to "Odoo 19" only. Finish 18 port before claiming it, or drop the claim.
  - Support duration: lovable says 30 days; competitor offers 90 free; left out of listing
    pending decision (recommend 90 to beat competitor).
  - Confirm support email hello@ vs support@.
  - icon.png low-res (~3KB) - swap for the crisp black "R Pay" tile.
  - React marketing site: host on a Fusion domain for SEO (optional, separate from listing).
- [~] Odoo 18 port WRITTEN at C:\Fusion\Revolut_odoo\build-18\payment_revolut (manifest
  18.0.1.0.0). Ported to the 18 framework: own _revolut_make_request helper (no 19
  _build_request_* / _send_api_request), _handle/_get_tx_from/_process_notification_data,
  _send_refund/capture/void take amount arg + super() creates child, std logging. const.py
  + HMAC webhook + order payload carried over. Full 14-test suite ported. All syntax/XML clean.
  NOT YET RUN — Docker Desktop was down; see build-18/RUN.md for exact install+test commands.
- [ ] STILL NEEDED: run 18 install+tests+€1 smoke (Docker); git repo for the MODULE on
  fusionassist (18.0 + 19.0 branches, separate from marketing repo); square icon.png;
  support 30/90 decision; Revolut-mark legal sanity-check.

## Status (2026-06-12)

Phase 0 ✅ (docs verified+cached, skeleton), Phase 1+2 code ✅ written and reviewed
(adversarial review against 19.0 source: 2 blockers + 4 majors found and fixed), unit +
HTTP tests written (signature valid/invalid/expired/rotated, state map, replay
idempotency, return flow, refund flow).

**Not yet done / needs Gerry or a live environment:**
- Sandbox Merchant account + sandbox secret key (Gerry) → run the test suite + manual
  end-to-end sandbox payment (kill-the-return-page test, multi-currency EUR+GBP).
- Test run requires an Odoo 19 environment (suggest staging on the erp box or a local
  Linux/WSL install): `odoo-bin -i payment_revolut --test-tags /payment_revolut -d <db>`
- Verify on sandbox: refund request accepts `merchant_order_data` (added for webhook
  correlation; remove if the API rejects it) and what dashboard-initiated refund
  webhooks carry as ext ref.
- Tour test for checkout (Phase 3), logging audit on a live log, proper icon +
  listing assets (Phase 5). Icon is currently a placeholder copied from core's
  revolut_pay.png — replace before listing.
