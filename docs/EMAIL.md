# Transactional email (v1.0)

ReelForge sends account, security, team, payment and support email through one interface (`app/mailer.py`) with two
adapters: **SMTP** (any provider or your own server) and **Resend** (HTTPS API). No provider is built in; choose one
in the admin UI.

## Setting it up

**Admin → System settings → Email**:

| Field | Notes |
| --- | --- |
| Enabled | Off by default: nothing is queued while it is off. |
| Provider | `smtp` or `resend` |
| From name, From email, Reply-to | The From address must belong to a domain the provider lets you send from. |
| SMTP host, port, security, username, password | Security: `starttls` (port 587), `ssl` (465) or `none` (only for a relay on the same machine). |
| Resend API key | For `resend`. |

The SMTP password and the Resend key are encrypted with the master key and never sent back to the page (the field
is write-only: keep, replace or clear). **Send test email** sends one message to your address (10 per hour) and shows
the result as a code (`auth_failed`, `connection_failed`, `timeout`, `rejected`, `provider_error`), never a server
response that could contain a secret.

On the sending domain, publish SPF and DKIM (and ideally DMARC) as the provider explains; without them, mail to
Gmail or Outlook may land in spam.

While email is off or failing:

* "Forgot password?" is hidden on the sign-in page. A user who forgot their password needs the operator: on the server, `python -m app.account_recovery reset-password --email …` sets a new one ([SECURITY.md](SECURITY.md#break-glass-recovery));
* invitations show a link to send another way;
* the "verify your email" banner is not shown, and inviting does not require a verified address.

## What is sent

Every message has an HTML part and a plain-text part, in the recipient's language: Vietnamese, English or Japanese,
the language chosen last in the app (saved on the account when it is switched). An invitation goes out in the
inviter's language, since the person invited may not have an account yet.

| Template | When |
| --- | --- |
| `verify_email` | Registration (and first-run setup), or *Resend verification email* |
| `welcome` | Once the address is verified |
| `password_reset` | *Forgot password* (only when the account exists; the page answers the same either way) |
| `password_changed` | After a password change or reset |
| `security_locked` | Five wrong passwords in a row blocked sign-in for 15 minutes |
| `security_2fa_enabled`, `security_2fa_disabled` | 2FA turned on or off (also by an administrator's reset) |
| `security_recovery_used` | A recovery code was used to sign in (with the number left) |
| `member_invite` | An invitation to a studio, with the role and the link (valid 7 days) |
| `account_created` | An administrator created the account |
| `payment_succeeded`, `payment_failed` | A payment was confirmed, or failed / was rejected (to the studio owner) |
| `subscription_activated` | An administrator activated or changed the plan |
| `support_reply` | Support answered a request (the reply itself stays in the app) |
| `test` | The admin test |

Links in emails point to the public origin (Admin → System settings → General). Tokens are in the URL fragment
(`#token=`), so they never appear in server or proxy logs.

## How it is delivered

1. A message is queued in `email_outbox` **inside the transaction** that caused it: when the change rolls back, no
   email leaves. Its parameters are encrypted; a dedupe key prevents duplicates (a payment receipt is sent once even
   if the provider calls back three times).
2. Right after the commit, the API wakes its delivery thread, which sends it within a second or two.
3. A failure is retried by the scheduler worker with back-off (1 min, 5 min, 30 min, 2 h, 6 h); after 6 attempts, or
   at once for a permanent error, it is marked failed.
4. Once sent or given up, the parameters are erased: only the template, the address and the status remain.

Admin → Verification shows whether email is configured and the outbox (queued, sent, failed in the last day); an alert
is raised when sending keeps failing. `reelforge_email_outbox` in `/internal/metrics` counts messages by status.

## Testing without sending

`tests/smtp_sink.py` is a tiny SMTP server that keeps every message (in memory, or as `.eml` files): the backend tests
and the browser tests (`e2e/`) point Admin → Email at it. No test sends real email.
