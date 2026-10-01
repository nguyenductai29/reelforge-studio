# Payments: VietQR (payOS) and cards (OnePAY)

Phase 14 adds card payments next to the existing VietQR checkout; Phase 19 lets a system admin configure both gateways from the web UI; Phase 20 adds manual VietQR, a bank-transfer QR confirmed by an admin, with no gateway. Both payment methods use the same order table, the same settlement code and the same subscription and credit rules. There is no second accounting system.

| Method (Billing UI) | Provider | Code | Configured by |
| --- | --- | --- | --- |
| VietQR / Bank Transfer | payOS | `app/billing.py`, `PayOSProvider` | Admin → Cổng thanh toán (encrypted); legacy: `payos` object in `instance/bootstrap.json` |
| VietQR / Bank Transfer (manual) | — (`bank_qr`) | `app/bank_qr.py`, `BankQRProvider` | Admin → Cổng thanh toán → VietQR → Manual (plain settings `payments.bank_qr.*`) |
| Credit / Debit Card | OnePAY | `app/payment_providers/onepay.py`, `OnePayProvider` | Admin → Cổng thanh toán (encrypted); legacy: `ONEPAY_*` environment variables |

## Architecture

```
POST /api/billing/checkout {plan_code, method}
  └─ payment_providers.for_method(method)  →  PayOSProvider | OnePayProvider
       └─ provider.checkout(...)            →  hosted payment page URL

Evidence (signed webhook/IPN, or a server-to-server status query)
  └─ payments.settle(db, provider, evidence)
       ├─ paid            → payments.apply_paid(db, order_code, amount, reference, provider=...)
       └─ failed/cancelled/expired → closes a *pending* order of the same provider
```

- **One order table:** `payment_orders`. `provider` is `payos` or `onepay`. `order_code` is the provider's order number; OnePAY receives it as `vpc_MerchTxnRef`. `provider_reference` stores the provider's transaction number (payOS reference, OnePAY `vpc_TransactionNo`). OnePAY needed no new column.
- **One settlement path:** `app/payments.py`. `apply_paid` is the only code that extends a subscription and posts the order's credits (`post_credit(..., "payment:<order id>")`). It locks the order row, so it is idempotent: a second callback finds `paid` and changes nothing.
- **Provider check:** `apply_paid` settles an order only when the evidence comes from the provider that created it. A OnePAY callback can never pay a VietQR order, and the reverse.
- **Exact amount:** the paid amount must equal the order's frozen `amount_vnd`. A mismatch raises and nothing is applied. The payOS webhook answers 400. The OnePAY IPN answers `responsecode=0&desc=amount-mismatch`.
- **Late payments:** an order that was closed as failed, cancelled or expired can still be paid by later evidence from the same provider.
- **Secrets:** they are never returned or logged. `GET /api/admin/payment-providers` and `GET /api/admin` expose only `{provider, method, configured, enabled, available, source}`. Order responses carry no `checkout_url`, raw payload or hash.
- **Card data:** ReelForge never collects, proxies or stores a card number or CVV. Buyers enter them on OnePAY's hosted page; VietQR is paid on payOS's hosted page.

## Billing flow

1. **Choose a plan.** The owner picks a plan on **Gói & credits**.
2. **Choose a method.** The dialog lists only the methods offered now (`GET /api/billing` → `methods`): **VietQR / Bank Transfer** and **Credit / Debit Card**. When none is available, the page says "No payment method is currently available" and shows no button. A plan is purchasable when it is active, has a price, and at least one method is offered; Admin → Plans shows which applies.
3. **Checkout.** `POST /api/billing/checkout` with `{plan_code, method: "vietqr" | "card"}` freezes the price and credits in a pending order and returns the hosted payment URL. An unconfigured method returns 503.
4. **Return.**
   - payOS sends the buyer back to `/billing`.
   - OnePAY sends the buyer back to `/api/billing/onepay/return`, which redirects to `/billing?payment=returned|failed|cancelled|invalid`.
5. **Order status.** The order shows in the history table (`GET /api/billing/orders?limit&offset`; columns Plan, Method, Amount, Status, Created, Paid, Order). **Kiểm tra / Check** asks the order's own provider (`POST /api/billing/orders/{id}/refresh`).
6. **Subscription and credits.** When a payment is confirmed, `apply_paid` extends the subscription by 30 days (from the old end when renewing the same plan) and awards the plan's credits once.

## OnePAY details

**Signing.** HMAC-SHA256, keyed with the hex-decoded `ONEPAY_HASH_KEY`. It covers the `key=value` pairs of every non-empty `vpc_*` and `user_*` field except `vpc_SecureHash` and `vpc_SecureHashType`, sorted by key and joined with `&`, and is sent as uppercase hex. `vpc_Amount` is VND × 100.

**Response codes.** `vpc_TxnResponseCode`:

| Code | Result |
| --- | --- |
| `0` | Paid |
| `99` | Cancelled by the buyer |
| `300` | Pending at the bank |
| Anything else | Failed |

**Evidence that can pay an order:**

- **IPN** (`GET|POST /api/webhooks/onepay`): OnePAY calls it server to server. A correctly signed result is settled at once. Answers:
  - `responsecode=1&desc=confirm-success` when the order is settled or already paid;
  - `responsecode=0&desc=confirm-fail | amount-mismatch | order-not-found` otherwise.
- **QueryDR** (`ONEPAY_QUERY_URL`, `vpc_Command=queryDR`): the server asks OnePAY for the current state. It is used by **Check**, by the admin **Refresh provider state** action and to confirm a browser return. `vpc_DRExists=N` means OnePAY has no result yet, so the order stays pending. The answer is signed and its signature is checked.

**What never pays an order:** the browser return alone.

- A signed "paid" return is confirmed with QueryDR before it is settled.
- Without QueryDR credentials, the order stays pending until the IPN arrives.
- Signed failed and cancelled returns close the pending order. Payment can still be retried later.
- A return with a bad signature is logged as `payment_return_rejected` and shown as `payment=invalid`.

## VietQR modes (Phase 20)

Buyers always see one method, **VietQR / Bank Transfer**. Admin → Thanh toán → Cổng thanh toán → VietQR chooses how it works; the mode is the system setting `payments.vietqr_mode`.

| Mode | How a buyer pays | What makes the order paid | Cost |
| --- | --- | --- | --- |
| **Automatic via payOS** (default) | Redirected to payOS's hosted page | payOS's signed webhook, or a Merchant API status check (Phase 14) | payOS fees |
| **Manual VietQR** | A QR shown inside ReelForge, paid from any banking app to the studio's own account | A system admin confirms the money arrived | No gateway |

**Switching modes only changes where new checkouts go.**

- **payOS orders keep settling.** Pending payOS orders still settle by webhook or Check while manual mode is on.
- **Manual orders stay confirmable.** Pending manual orders can still be confirmed while payOS mode is on.
- **Setting the mode.** Saving the VietQR tab sets the mode in the same request (`use_for_vietqr`).

### Manual VietQR

**Configuration (system admin):**

- the bank, picked from a list of NAPAS BINs or typed as a 6-digit BIN;
- the account number;
- the account holder (upper-case, no diacritics);
- the transfer content prefix (default `RF`, 1–8 upper-case letters or digits);
- an optional note for buyers, and the confirmation time (SLA) to show.

These are not secret: buyers see them on the QR. They are stored as plain system settings (`payments.bank_qr.*`), audited by name like every setting. A live **QR preview** (100,000 VND, content `RFTEST01`) shows what buyers will scan. A save that turns the method on with a missing field is refused (`missing`, with the field).

**The QR** (`app/bank_qr.py`) is the NAPAS VietQR payload, which every Vietnamese banking app reads:

- **Format:** the EMVCo merchant-presented format, with GUID `A000000727` and service `QRIBFTTA`.
- **Contents:** the bank's BIN and the account; the **exact plan price** in VND (`54`); the transfer content (`62` → `08`); a CRC-16/CCITT-FALSE checksum (`63`).
- **Rendering:** drawn on the server as SVG with `segno`. No image service is called, and no secret is involved.
- **Not proof:** generating a QR is never evidence of payment.

**The transfer content is unique per order.** It is the prefix plus the order's 13-digit order code, for example `RF1234567890123`, so simultaneous buyers are never confused. It is stored on the order (`provider_reference`) when the order is created, so changing the prefix later does not change existing orders.

**The flow:**

1. **The buyer checks out.** They choose a plan → VietQR. `POST /api/billing/checkout` creates a pending order with provider `bank_qr` and returns `{order_id, checkout_url: null, transfer}`: the QR (an SVG data URI), bank, account, holder, exact amount, content, note and SLA. Nothing is paid yet; no subscription or credit changes.
2. **The buyer transfers** the exact amount with the exact content, then presses **Tôi đã chuyển khoản**. This calls `POST /api/billing/orders/{id}/transferred`:
   - the owner's own studio only (other studios get 404);
   - it records `transfer_reported_at` and a `transfer_reported` event;
   - it notifies every system admin in real time (`payment.transfer_reported`, linking to Admin → Thanh toán filtered on **Chờ xác nhận**).

   Reporting twice changes nothing. The order stays `pending` and shows **Chờ xác nhận**. **Xem QR** (`GET /api/billing/orders/{id}/transfer`) shows the QR again.
3. **A system admin checks the bank account,** then in Admin → Thanh toán chooses **Xác nhận đã nhận tiền** on the order. The dialog states "Confirm that 199,000 ₫ has been received for order RF… (buyer)". `POST /api/admin/payments/{id}/confirm` with `{amount_vnd}`:
   - must equal the order's amount (else 422 `amount_mismatch`);
   - settles through the **shared** `payments.apply_paid` path (provider `bank_qr`). It extends the subscription and posts the plan's credits **once**, under the order's row lock; a second confirm answers 409;
   - writes a `confirmed` event with the admin and the amount (`payment_order_events`, listed by `GET /api/admin/payments/{id}/events`).
4. **Or the admin rejects it.** **Từ chối / không thấy tiền** (`POST …/reject`, optional note) fails the order and notifies the owner. If the money turns up later, the order can still be confirmed, exactly as a late payment of any provider.

**Who can do what:**

- Only system admins can confirm or reject. Studio owners, including the buyer, get 403.
- A non-manual order (payOS, OnePAY) can never be confirmed by hand (409): those settle only from provider evidence.
- **Check** on a manual order asks nobody and changes nothing.

**Disabling.** Disabling manual VietQR stops new checkouts. Pending manual orders can still be confirmed.

## Configuration (Phase 19: admin-managed)

A system admin configures both gateways in **Admin → Thanh toán → Cổng thanh toán**. No SSH, file edit or restart is needed: a saved change applies to the next checkout and callback in every API process.

**Who can change it.** Only system admins. Every payment-config route checks this on the server, and studio owners and other users get 403. Owners keep what they had: choose a plan and a method, pay, and see their own orders. `GET /api/billing` returns only the methods buyers may choose now, never configuration.

### Where credentials come from

`app/payment_config.py` is the only module that reads payment credentials. Checkout, the payOS webhook, the OnePAY return and IPN, status checks (**Check**, admin **Refresh**), the admin view and readiness all use what it resolves. For each provider, in order:

1. **Admin:** the configuration a system admin saved, from the `payment_provider_configs` table (encrypted).
2. **Legacy:**
   - payOS: the `payos` object of `instance/bootstrap.json`;
   - OnePAY: the `ONEPAY_*` variables of the runtime environment.

   These are deployments configured before Phase 19. They keep working unchanged and are shown as *Bootstrap* or *Environment*. Changing them still needs a file edit and an API restart.
3. **Missing.**

**Rules:**

- **Admin wins.** Once an admin saves credentials for a provider, the legacy source is ignored for it; the admin view still notes that one exists.
- **No silent fallback.** A saved configuration that cannot be decrypted (the key is missing or was changed) is reported as an error, and the provider is unavailable. The server never falls back silently to the legacy credentials, which may belong to another merchant account or to production.
- **Nothing is imported.** Saving never copies legacy values into the database. To switch a provider to admin-managed configuration, enter every required field once.

### The enabled switch

Each provider has an **enabled** switch, on by default for legacy deployments.

- **Offered to buyers** only when enabled and its credentials are complete and valid. `GET /api/billing` → `methods` lists `vietqr` and/or `card` on that basis, and a checkout for any other method answers 503 "This payment method is not available".
- **Disabling stops new checkouts only.** Webhooks, IPNs, returns and status checks of existing orders still use the provider's credentials, so a pending order still settles and a paid order is untouched. Credits are still posted once by `apply_paid`.
- **Without saved credentials** (a legacy deployment), disabling records only the switch.

### Encrypted at rest

All of a provider's credentials are stored together as one Fernet-encrypted JSON object in `payment_provider_configs.config_ciphertext`; no secret has a column of its own. The encryption is done by `app/secret_box.py`.

**Which key.** It reuses the master key (Phase 20: `/etc/reelforge/master.key`, or the legacy `REELFORGE_TOKEN_ENCRYPTION_KEY`; see [SYSTEM_CONFIGURATION.md](SYSTEM_CONFIGURATION.md)), the key that already encrypts OAuth tokens:

- **Why share it.** Both keys would sit in the same runtime file and be read by the same API process, so they would be lost or leaked together. A second key would add a backup burden without a security boundary.
- **No swapping.** Each purpose gets its own key derived with HKDF-SHA256 (`reelforge:payment-config:payos`, `…:onepay`). A payment ciphertext cannot be decrypted as an OAuth token, or as the other provider's configuration.
- **No key, no save.** Without the variable, saving answers 422 `key_missing` and nothing is generated. Set it once:
  1. Run `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
  2. Put the output in the runtime file.
  3. Restart the API.

**Back the key up with the database.** A database backup alone cannot reveal any payment secret, and also cannot use them without the key.

**Never returned.** Saved values never leave the server:

- API responses show each field as configured / missing / invalid;
- only the payOS client ID and the OnePAY merchant ID are shown, masked (`abcd…wxyz`);
- errors name the field, never the value;
- logs record field names only.

### payOS (VietQR)

| Field | Required | Shown |
| --- | --- | --- |
| Client ID | yes | masked |
| API Key | yes | configured / missing |
| Checksum Key | yes | configured / missing |

**Webhook URL to register with payOS:** `https://<public frontend>/api/webhooks/payos`.

payOS has no sandbox endpoint and no read-only call that proves the keys, so **Kiểm tra cấu hình** validates locally and reports the remote check as unsupported. Verify it with one small real payment.

### OnePAY (cards)

| Field | Required | Shown |
| --- | --- | --- |
| Merchant ID | yes | masked |
| Access Code | yes | configured / missing |
| Hash Key | yes | configured / missing / invalid (must be hex) |
| QueryDR User, QueryDR Password | recommended (both or neither) | configured / missing |

**Mode.** The admin picks a mode instead of typing gateway URLs:

| Mode | Payment page | QueryDR |
| --- | --- | --- |
| Sandbox | `https://mtf.onepay.vn/paygate/vpcpay.op` | `https://mtf.onepay.vn/msp/api/v1/vpc/invoices/queries` |
| Production | `https://onepay.vn/paygate/vpcpay.op` | `https://onepay.vn/msp/api/v1/vpc/invoices/queries` |
| Advanced (custom) | entered by the admin (https only) | entered by the admin (https only) |

Confirm the sandbox endpoints against OnePAY's merchant integration guide before the first sandbox test; if OnePAY gives you others, use **Advanced**.

**The mode is always visible.** The tab and the panel show a **SANDBOX** or **PRODUCTION** badge.

**Going live needs confirmation.** Turning on production asks: "You are enabling the production card gateway. Real customer payments can now be accepted." The confirmation is required whether the admin switches to production or re-enables a production gateway. The API enforces it: without `confirm_production: true` it answers 409 `confirm_production`.

**URLs to register with OnePAY:**

- IPN URL: `https://<public frontend>/api/webhooks/onepay`;
- Return URL: `https://<public frontend>/api/billing/onepay/return`, also sent with each checkout.

Both come from System Settings → `frontend_origin`.

**Without QueryDR credentials**, the IPN still confirms payments, but **Check** and confirming a returning buyer cannot.

**Legacy variables.** When no admin configuration is saved, the legacy `ONEPAY_*` variables apply as before:

| Variable | Required | Meaning |
| --- | --- | --- |
| `ONEPAY_MERCHANT_ID` | yes | `vpc_Merchant` |
| `ONEPAY_ACCESS_CODE` | yes | `vpc_AccessCode` |
| `ONEPAY_HASH_KEY` | yes | Secure hash key (hex) |
| `ONEPAY_PAYMENT_URL`, `ONEPAY_QUERY_URL` | no | Default production; the mode is derived from the host |
| `ONEPAY_QUERY_USER`, `ONEPAY_QUERY_PASSWORD` | recommended | QueryDR credentials |

### Saving: write-only secrets

`PUT /api/admin/payment-config/{payos|onepay}` takes each credential as an explicit update, so an empty string is never ambiguous:

```json
{
  "enabled": true,
  "mode": "sandbox",
  "merchant_id": {"action": "keep"},
  "hash_key": {"action": "replace", "value": "…"},
  "query_password": {"action": "clear"},
  "confirm_production": false
}
```

**Actions:**

- `keep` keeps the saved value. It is also the default when a field is omitted, and what the form sends for a field left blank.
- `replace` sets a new value. An empty replacement is refused.
- `clear` removes an optional value; clearing a required one is refused.

`mode`, `payment_url` and `query_url` apply to OnePAY only, and the URLs only in custom mode.

**Validation.** A save that touches credentials or the mode must produce a complete, valid configuration, or nothing is written: 422 with `{code, field}`. The codes are:

- `missing`, `invalid_value`, `invalid_hash_key`, `invalid_url`, `query_incomplete`;
- `key_missing`, `cannot_decrypt`;
- `invalid_request`, used for malformed JSON or wrong types, without echoing the input.

A save that keeps every field and the mode changes only the switch.

**Other routes:**

| Route | Effect |
| --- | --- |
| `GET /api/admin/payment-config` | Per provider: `enabled`, `configured` (credentials valid), `available` (offered to buyers), `source` (`admin`/`bootstrap`/`environment`/`missing`), `legacy_source`, `mode`, `fields`, `issues`, `updated_at`, `updated_by`, `urls` and `query_configured` (OnePAY), the URLs to register, activity, and the last changes. Also `any_available` and whether the encryption key is set |
| `POST /api/admin/payment-config/{provider}/disable` | Stop new checkouts |
| `POST /api/admin/payment-config/{provider}/enable` | `{confirm_production}`. Needs valid credentials |
| `POST /api/admin/payment-config/{provider}/check` | `{remote}`. Validates the configuration in use (see below) |

`POST /api/admin/payment-config/check` with `{provider, remote}` is kept for Phase 18 clients.

All modifying routes are same-origin protected and system-admin only.

### Configuration check

The check never creates a payable order, charges, activates a plan or awards credits.

- **Local:** validates the configuration in use, exactly as checkout would build it.
- **OnePAY remote:** sends one signed QueryDR about the reference `RFCHECK<time>`, which no checkout ever used. A signed "no such transaction" proves the endpoint, the QueryDR credentials and the hash key.
- **payOS remote:** reported as unsupported (see above).

### Audit trail

`payment_config_audit` records each change: `created` (first admin-managed credentials), `updated`, `enabled`, `disabled` and `tested`, with the admin and the time.

- Its `metadata_json` holds changed field **names** (`{"changed": ["api_key"], "mode": "sandbox"}`) and check statuses, never a value.
- The admin view lists the last eight entries per provider.
- The log events `payment_config_saved` and `payment_config_checked` carry the same names and statuses.

### Activity

The time of the last verified payOS webhook, OnePAY IPN, status query and successful check is kept per provider in the system setting `payment_activity`, timestamps only. The admin view shows it.

### Sandbox → production (OnePAY)

1. **Configure the sandbox.** Save Sandbox mode with OnePAY's test merchant, access code, hash key and QueryDR credentials. Register the IPN and Return URLs for the test merchant.
2. **Check it.** Press **Kiểm tra cấu hình**, then **Kiểm tra với OnePAY (QueryDR)**.
3. **Test payments.** Pay with OnePAY's test card. Then:
   - confirm the order turns paid;
   - confirm the IPN time appears under activity;
   - confirm the plan's credits are posted once.

   Cancel one payment, and press **Check** on an order.
4. **Go live.** Enter the production credentials from the merchant contract, select **Production**, save and confirm. Register the production IPN and Return URLs.
5. **Pay once for real.** Make one small real payment and check that the credits are posted once.
6. **Record it.** Tick each step in Admin → Kiểm định ([LIVE_VERIFICATION.md](LIVE_VERIFICATION.md)).

### Disaster recovery

| Situation | What happens | Fix |
| --- | --- | --- |
| Database restored with its key | Everything works | — |
| Database restored, key lost or changed | Saved gateways show "cannot decrypt" and are unavailable; legacy credentials are **not** used instead | Restore the old master key file (or `REELFORGE_TOKEN_ENCRYPTION_KEY`) and restart the API, or re-enter every secret of each gateway in the admin UI. The admin UI is the only route for payment secrets; OAuth tokens need their channels reconnected |
| Key missing on a new server | Admin-managed gateways cannot be saved (`key_missing`); legacy credentials still work | Set the key, restart the API |
| Wrong credentials saved | Checkouts or callbacks fail; pending orders stay pending | Save the right values (blank fields keep the others), then **Check** pending orders |
| Need to stop payments now | — | **Tắt** on the gateway: new checkouts stop at once; existing orders still settle |

`payment_provider_configs` is ordinary data: it is in every `pg_dump`. Keep the runtime file, which holds the key, backed up separately from the dump.

## Admin

- **Admin → Payments** lists every order, server-paginated (`GET /api/admin/payments?q&provider&status&limit&offset`). `q` matches the owner email, studio name, provider reference, or the order code when it is all digits.
- **Refresh provider state** (`POST /api/admin/payments/{id}/refresh`) runs the same provider query as **Check**.
- There is deliberately no "mark as paid" action. Manual plan changes stay in **Studios & credits** and do not charge anyone.
- Each provider's state (available, disabled, not configured) is shown above the table; **Cổng thanh toán** opens the gateway configuration. With no gateway enabled, the Admin header warns.
- When an order is paid but cannot be applied (`paid_unapplied`), system admins get a notification ([NOTIFICATIONS.md](NOTIFICATIONS.md)). Owners are notified when their payment succeeds or fails.

## Tests

`tests/test_payments.py` runs offline. OnePAY is replaced by an in-process fake: requests are signed with a test key, and `payment_providers.http_client` is patched. It covers:

- OnePAY checkout parameters and signature;
- forged signatures, wrong amounts and unknown orders;
- a valid IPN, then a duplicate (credits posted exactly once);
- a browser return before and after QueryDR confirms it;
- failed and cancelled returns;
- payOS VietQR checkout and webhook, unchanged;
- cross-provider evidence;
- renewal, upgrade, admin refresh and history pagination.

`tests/test_phase18.py` covers the admin setup view:

- only system admins can read it or run checks;
- masked identifiers, and no secret anywhere in the response;
- OnePAY's sandbox/production mode;
- the read-only QueryDR check against a mocked client;
- the activity timestamp after a successful check.

`tests/test_phase19.py` covers admin-managed configuration (payOS through a fake SDK class, OnePAY through `httpx.MockTransport`; any other request fails the test):

- **Access:** a normal user and a studio owner get 403 on every route.
- **Storage:** the stored ciphertext and every other table hold no plaintext secret.
- **Never returned:** sentinel secrets never appear in responses, errors (bad hash key, oversized values, wrong types, broken JSON), the audit trail or logs.
- **Key problems:** with the key missing nothing is saved. With another key the provider is unavailable and the bootstrap credentials are not used. A payOS ciphertext pasted into OnePAY's row never decrypts.
- **payOS:**
  - bootstrap fallback, then the admin override applied to the next checkout and webhook (the old checksum key is refused);
  - updating only the API key, keep versus empty replace, clearing a required key refused;
  - disable and enable; a pending order settling by webhook and by Check while disabled; the paid order untouched; credits posted once;
  - disabling a bootstrap-configured provider without importing its values;
  - a test that creates no order and awards no credit.
- **OnePAY:**
  - environment fallback, then the admin sandbox configuration used for checkout, the IPN (the environment's hash key and merchant refused) and QueryDR (sandbox URL, saved user and password);
  - updating one secret; clearing and half-setting QueryDR;
  - invalid values refused without changes;
  - custom endpoints; production refused without confirmation and accepted with it;
  - disable with a pending IPN settling; re-enabling production needing confirmation;
  - the read-only QueryDR check; the audit trail; readiness.

`tests/test_phase19_migration.py` runs 0017 → 0018 and back, keeping orders, subscriptions, the credit ledger and `payment_activity`.

`tests/test_phase20.py` covers manual VietQR:

- the NAPAS payload (CRC check value, exact amount, unique content per order);
- admin-only configuration and preview;
- checkout returning the QR;
- cross-studio 404s;
- the transfer report and the admin notification;
- confirmation refused for owners, for the wrong amount and for non-manual orders;
- settlement exactly once;
- rejection, then a late confirmation;
- disabling;
- payOS automatic mode and its webhook unchanged;
- OnePAY unchanged.

`tests/test_phase20_migration.py` runs 0018 → 0019 and back.

Live payments have not been verified. See [Live verification](#live-verification).

## Live verification

Before accepting customers, an operator must check, with the OnePAY sandbox and then one small real transaction:

1. The checkout page opens with the right amount.
2. The return lands on `/billing?payment=returned` and the order turns paid after QueryDR or the IPN.
3. OnePAY's IPN reaches `/api/webhooks/onepay` through the proxy and receives `responsecode=1`.
4. A cancelled payment shows `payment=cancelled`.
5. The credits appear once in **Gói & credits**.

Repeat steps 1, 3 and 5 for payOS if its configuration changed.

Follow [Sandbox → production](#sandbox--production-onepay) for the card gateway. Configure payOS in **Cổng thanh toán → VietQR**, press **Kiểm tra cấu hình**, make one small payment, and confirm the webhook time appears under activity.

Record the results in Admin → Kiểm định ([LIVE_VERIFICATION.md](LIVE_VERIFICATION.md)).
