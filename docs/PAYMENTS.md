# Payments: VietQR (payOS) and cards (OnePAY)

Phase 14 adds card payments next to the existing VietQR checkout. Both payment methods use the same order table, the same settlement code and the same subscription and credit rules. There is no second accounting system.

| Method (Billing UI) | Provider | Code | Configured by |
| --- | --- | --- | --- |
| VietQR / Bank transfer | payOS | `app/billing.py`, `PayOSProvider` | `payos` object in `instance/bootstrap.json` (unchanged) |
| Bank card | OnePAY | `app/payment_providers/onepay.py`, `OnePayProvider` | `ONEPAY_*` environment variables |

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
- **Secrets:** they are never returned or logged. `GET /api/admin/payment-providers` and `GET /api/admin` expose only `{provider, method, configured}`. Order responses carry no `checkout_url`, raw payload or hash.

## Billing flow

1. **Choose a plan.** The owner picks a plan on **Gói & credits**.
2. **Choose a method.** The dialog lists only configured methods (`GET /api/billing` → `methods`). When none is configured, the page says so and shows no button.
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

## Configuration

**payOS:** unchanged. See README → "VNQR checkout with payOS" and `docs/home-server-deployment.md` § 15. Webhook: `https://<public frontend>/api/webhooks/payos`.

**OnePAY:** set these in `.env.runtime` on the API server. The OnePAY merchant contract supplies the values.

| Variable | Required | Meaning |
| --- | --- | --- |
| `ONEPAY_MERCHANT_ID` | yes | `vpc_Merchant` |
| `ONEPAY_ACCESS_CODE` | yes | `vpc_AccessCode` |
| `ONEPAY_HASH_KEY` | yes | Secure hash key (hex) |
| `ONEPAY_PAYMENT_URL` | no | Payment page. Default `https://onepay.vn/paygate/vpcpay.op`; use OnePAY's sandbox URL while testing |
| `ONEPAY_QUERY_URL` | no | QueryDR endpoint. Default `https://onepay.vn/msp/api/v1/vpc/invoices/queries` |
| `ONEPAY_QUERY_USER`, `ONEPAY_QUERY_PASSWORD` | recommended | QueryDR credentials. Without them, a browser return cannot be confirmed, and **Check** reports that the provider is unavailable |

Both URLs must be `https`. An incomplete or invalid configuration means card payment is "not configured": it is hidden in Billing and shown as **Missing** in Admin → Payments.

Register these addresses with OnePAY:

- **Return URL:** `https://<public frontend>/api/billing/onepay/return` (sent with each checkout; it comes from System Settings → `frontend_origin`).
- **IPN URL:** `https://<public frontend>/api/webhooks/onepay`.

The frontend proxies `/api/*` to the API, as for the payOS webhook. Restart the API after changing the variables.

## Admin

- **Admin → Payments** lists every order, server-paginated (`GET /api/admin/payments?q&provider&status&limit&offset`). `q` matches the owner email, studio name, provider reference, or the order code when it is all digits.
- **Refresh provider state** (`POST /api/admin/payments/{id}/refresh`) runs the same provider query as **Check**.
- There is deliberately no "mark as paid" action. Manual plan changes stay in **Studios & credits** and do not charge anyone.
- Provider readiness (Configured/Missing) is shown above the table.
- When an order is paid but cannot be applied (`paid_unapplied`), system admins get a notification ([NOTIFICATIONS.md](NOTIFICATIONS.md)). Owners are notified when their payment succeeds or fails.

## Admin setup view

Admin → Thanh toán → **Cấu hình cổng** (Phase 18A) shows what each provider needs.

**Who can see it.** Only system admins: `GET /api/admin/payment-config` and `POST /api/admin/payment-config/check` answer 403 to anyone else, studio owners included. Owners keep what they had: choose a plan and a method, pay, and see their own orders. `GET /api/billing` still returns only the available `methods`, never configuration.

**Where secrets live.** Credentials stay where they are and are never moved into the database or editable from the browser:

- payOS in the `payos` object of `instance/bootstrap.json`;
- OnePAY in the `ONEPAY_*` variables of `.env.runtime`.

To change them, edit that file on the server and restart the API.

| Provider | Field (where it is set) | Shown as |
| --- | --- | --- |
| payOS | `payos.client_id` (bootstrap) | Masked (`abcd…wxyz`) |
| payOS | `payos.api_key`, `payos.checksum_key` (bootstrap) | Configured / missing |
| OnePAY | `ONEPAY_MERCHANT_ID` | Masked |
| OnePAY | `ONEPAY_ACCESS_CODE`, `ONEPAY_QUERY_USER`, `ONEPAY_QUERY_PASSWORD` | Configured / missing |
| OnePAY | `ONEPAY_HASH_KEY` | Configured / missing / invalid (not hex) |
| OnePAY | `ONEPAY_PAYMENT_URL`, `ONEPAY_QUERY_URL` | In full (they are not secret), marked when the default is used |

**Also shown:**

- OnePAY's mode, from the payment URL:
  - **Sandbox:** `mtf.onepay.vn`;
  - **Production:** `onepay.vn`;
  - **Custom:** any other host.
- Whether QueryDR is configured.
- The addresses to register with the provider, each with a copy button:
  - payOS webhook `…/api/webhooks/payos`;
  - OnePAY IPN `…/api/webhooks/onepay`;
  - OnePAY return `…/api/billing/onepay/return`.

  They come from System Settings → `frontend_origin`.
- Activity: when the last verified payOS webhook, OnePAY IPN, status query and successful check arrived. This is kept in the system setting `payment_activity`, timestamps only.

The response never contains a key, access code, hash key, password, the bootstrap file or the runtime environment. The tests plant sentinel secrets and assert that none of them appears.

**Kiểm tra cấu hình** (`POST /api/admin/payment-config/check` with `{provider, remote}`) never charges and never creates a checkout:

- **On the server** (`remote: false`): validates the configuration locally, as the checkout would. For OnePAY this covers the required fields, hex hash key and https URLs; missing QueryDR credentials are a warning.
- **With OnePAY** (`remote: true`, OnePAY only): sends one signed QueryDR about the reference `RFCHECK<time>`, which no checkout ever used. A signed "no such transaction" answer proves the endpoint, credentials and hash key. Nothing is read or written for any real order.
- **payOS** has no read-only call that proves its keys without creating a payment link, so its remote check is reported as unsupported. Verify payOS with one small real payment (Admin → Kiểm định checklist).

Each check is logged as `payment_config_checked` with the statuses only.

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

Live payments have not been verified. See [Live verification](#live-verification).

## Live verification

Before accepting customers, an operator must check, with the OnePAY sandbox and then one small real transaction:

1. The checkout page opens with the right amount.
2. The return lands on `/billing?payment=returned` and the order turns paid after QueryDR or the IPN.
3. OnePAY's IPN reaches `/api/webhooks/onepay` through the proxy and receives `responsecode=1`.
4. A cancelled payment shows `payment=cancelled`.
5. The credits appear once in **Gói & credits**.

Repeat steps 1, 3 and 5 for payOS if its configuration changed.

Before the sandbox test, open **Cấu hình cổng**:

1. Check that the mode reads **Sandbox**.
2. Run **Kiểm tra cấu hình** with OnePAY.
3. After the payment, confirm that the IPN time appears under activity.

Record the results in Admin → Kiểm định ([LIVE_VERIFICATION.md](LIVE_VERIFICATION.md)).
