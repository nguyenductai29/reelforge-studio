# ReelForge Studio v1.0 — release status

> **Status: RELEASE_CANDIDATE.** Not ready for the `v1.0.0` tag: the Terms of Service and the Privacy Policy are still
> templates, and the deployment of the release candidate, CI on it and the 46 mandatory gates are not recorded as
> passed anywhere this audit could see (the gates live in the production database, which it did not read). This file
> says so until the release report says `READY_FOR_TAG`; it is updated by the operator, from what was actually checked.

Audited on 2026-10-03 (release closure). Every value below comes from the repository, GitHub's public API or a run on
a development machine; nothing about the production server is assumed.

| | |
| --- | --- |
| Status | `RELEASE_CANDIDATE` |
| Branch | `feat/studio-foundation` (in step with `origin` at the audit) |
| Audited source commit | `56ada10845e595450f4c4aceccbf670a2715babc` ("redesign layout"): the code of the release candidate |
| Release candidate | The release-closure commit on top of `56ada10`, the one that brings this version of this file: `git log -1 --format=%H -- docs/V1_RELEASE_STATUS.md` prints it. It changes documentation (`docs/`, `README.md`, `e2e/README.md`), a usage comment in `deploy/release-preflight.sh` and the documentation checks in `tests/test_phase28.py`; no application code, no migration (`git diff --stat 56ada10 <candidate>` shows it). A later fix makes a new candidate ([below](#a-bug-found-during-verification)) |
| Migration head | `0026_change_production_origin` (one head; 0026 changes data only: the production origin) |
| Production origin | `https://reelforge.mul-service.com` (Cloudflare Tunnel → `http://127.0.0.1:3001`; the API on `127.0.0.1:8000`) |
| Deployed on the server | MANUAL: the repository holds no record of a deployment of the release candidate. `./deploy.sh` on the server, then record `release_deploy` (commit, date, who) |
| CI | PASS on `56ada10`: GitHub Actions, all 14 check runs successful (push and pull request; finished 2026-10-02 16:44 UTC). MANUAL for the release candidate: **CI must be green on the exact deployed release commit** (`release_ci_green`) |
| Pre-flight | Code audit PASS; on the server MANUAL (`release_preflight`) |
| Release report | Code audit PASS; on the server MANUAL. With no gate recorded it can only say `RELEASE_CANDIDATE` |
| Manual gates | 68 in Admin → Verification: 46 mandatory, 22 optional (passed or not applicable). Recorded: none known (MANUAL) |
| Tag readiness | Not ready: `RELEASE_CANDIDATE` ([what remains](#remaining-blockers)) |

## How the release is decided

The gates are listed in [RELEASE_V1_CHECKLIST.md](RELEASE_V1_CHECKLIST.md); each is recorded in one place: the
pre-flight (`bash deploy/release-preflight.sh`), CI, or Admin → Verification (by the person who checked it,
[LIVE_VERIFICATION.md](LIVE_VERIFICATION.md)). The release may be tagged only when **all** of these hold on the
deployed commit:

1. CI is green on that exact commit (backend on SQLite with Python 3.11 and 3.14, backend on PostgreSQL, migrations,
   frontend on Node 20 and 22, Playwright). A local run never replaces it.
2. The pre-flight has no FAIL (`--expect-commit <commit>`).
3. Every gate in Admin → Verification is *Passed*, or *Not applicable* where that is allowed (payOS, OnePAY, Runway,
   TikTok, Facebook, and their domain-change gates, when the installation does not use them).
4. `bash deploy/release-report.sh` says `READY_FOR_TAG`.

Nothing passes by itself: no tool records a gate, and a gate nobody recorded counts as not passed.

The status moves one step at a time, never skipping one; each step is written in the history below:

| Status | When |
| --- | --- |
| `RELEASE_CANDIDATE` | Until the report says `READY_FOR_TAG` (now) |
| `READY_FOR_TAG` | The report on the server says `READY_FOR_TAG` for the deployed commit |
| `RELEASED v1.0.0` | Only after the tag `v1.0.0` was created on that commit and pushed |

| Date | Status | Commit | Evidence |
| --- | --- | --- | --- |
| 2026-10-03 | `RELEASE_CANDIDATE` | `56ada10` (audited code) and the release-closure commit | This audit; CI green on `56ada10`; no gate recorded |

## Status by area

PASS, FAIL, MANUAL (not yet checked by a person on the production server) or NOT_APPLICABLE. Update a row only from
a real check, with the date and who.

| Area | Status | Evidence / what remains | Date / by |
| --- | --- | --- | --- |
| CI on the audited commit `56ada10` | PASS | GitHub Actions check runs of `56ada10845e5…`: backend SQLite (Python 3.11, 3.14), backend PostgreSQL, Alembic, frontend (Node 20, 22), Playwright, each successful on the push and on the pull request: 14 / 14. Read from `api.github.com/repos/nguyenductai29/reelforge-studio/commits/56ada10…/check-runs` | 2026-10-02 16:44 UTC (read 2026-10-03) |
| CI on the release candidate | MANUAL | The closure commit runs CI when it is pushed; record `release_ci_green` from the run of the exact deployed commit | |
| Automated suites, development machine | PASS | [RELEASE_V1_CHECKLIST.md](RELEASE_V1_CHECKLIST.md#automated-evidence-development-machine-2026-10-03-release-closure). Evidence only: not a gate | 2026-10-03 |
| Deployment tooling (code audit) | PASS | `deploy.sh` read: pull (and the commit), bootstrap (no development override, database answering), master key (`deploy/ensure-master-key.sh`), dependencies, migrations and the head, frontend build, restart of the API, the frontend and every enabled worker, every restarted unit checked, `/health/ready` and the frontend, unit files that differ or need a reload, timers; exit status 1 on any failure | 2026-10-03 |
| Pre-flight and report (code audit) | PASS | `app/release_check.py` read: commit and clean tree, database and head, master key, services and workers, ports, `/health/live` and `/ready`, storage, backups, FFmpeg, origin and Secure cookies, OAuth redirect overrides, setup closed, administrators' 2FA, legacy values. Verdict unchanged: `READY_FOR_TAG` only without a pre-flight FAIL, with CI on the commit neither failing, pending nor missing (when GitHub cannot be asked, the recorded `release_ci_green` decides), and every gate passed (or not applicable where allowed) | 2026-10-03 |
| Admin → Verification against the checklist | PASS | 68 gates in 12 groups (46 mandatory, 22 optional, 13 paid), unique keys, each named in [RELEASE_V1_CHECKLIST.md](RELEASE_V1_CHECKLIST.md) and each checklist key a gate (`tests/test_phase28.py`); no gate names the old domain | 2026-10-03 |
| Domain change | MANUAL | The stored origin moves with migration 0026; Cloudflare, Google, TikTok, Meta, payOS and OnePAY are updated by hand ([below](#domain-change)) | |
| Deployment (`./deploy.sh`) | MANUAL | `release_deploy` | |
| Pre-flight on the server | MANUAL | `release_preflight`: source, database and head, master key, services and timers, ports, health, storage, backups, FFmpeg, configuration, administrators | |
| Platform | MANUAL | `migration_upgraded`, `storage_on_hdd`, `ffmpeg_verified`, `master_key_file` | |
| Email | MANUAL | `email_test_sent`, `email_dns` (SPF, DKIM, DMARC), `email_verification`, `email_password_reset`, `email_password_changed`, `email_invitation`, `email_support_reply` | |
| AI providers | MANUAL | Gemini text, Gemini TTS, transcription; Runway image and video (or NOT_APPLICABLE); credits once and the refund path | |
| Render | MANUAL | Voice, subtitles and music; slideshow (Article → Video, Product Video); Movie Recap; Movie Review | |
| Payments: manual VietQR | MANUAL | `bank_qr_round_trip` (mandatory) | |
| Payments: payOS | MANUAL | Or NOT_APPLICABLE if payOS is not used | |
| Payments: OnePAY | MANUAL | Sandbox, then production; or NOT_APPLICABLE if cards are not offered | |
| Publishing | MANUAL | YouTube private, scheduled post; TikTok and Facebook (or NOT_APPLICABLE) | |
| Cloudflare and security | MANUAL | HTTPS and headers, cookie flags, foreign Origin, real and forged client addresses, 2FA, sessions, rate limits, SSE through Cloudflare | |
| Backups and recovery rehearsal | MANUAL | Timer and dump, off-server copies of dump and key, rehearsal into `reelforge_restore_test` | |
| Media backup | MANUAL | Manifest, copy, verify | |
| Operations | MANUAL | Alerts, support round trip and account closure, maintenance timer, cleanup dry run | |
| Reboot | MANUAL | `server_reboot` | |
| Legal | MANUAL | 11 placeholders still in the templates, in all three languages ([below](#legal)); lawyer review pending | |

Accepted pre-flight warnings (write each one and why): none yet.

## Remaining blockers

No code or configuration blocker was found in the release closure (BLOCKER or HIGH: none; the documentation that was
out of date was corrected). What keeps the release a candidate is outside the repository:

1. **Deployment and pre-flight:** the release candidate deployed with `./deploy.sh`, the pre-flight without FAIL on
   it (`release_deploy`, `release_preflight`).
2. **CI on the release candidate:** green on the exact deployed commit (`release_ci_green`).
3. **Manual gates:** every mandatory gate passed, every optional one passed or not applicable, as recorded in Admin →
   Verification ([LIVE_VERIFICATION.md](LIVE_VERIFICATION.md) has each procedure).
4. **Legal:** the Terms of Service and the Privacy Policy filled in and reviewed by a lawyer; `TERMS_VERSION` set
   (`legal_terms_reviewed`, `legal_privacy_reviewed`).

Low findings documented, not fixed in v1.0:

* [V1_RELEASE_AUDIT.md § D](V1_RELEASE_AUDIT.md#d-security-audit-findings), findings 16–20.
* `npm ci` reports two advisories (one high, one moderate), both in the PostCSS copy that `next@15.5.26` bundles
  (`postcss@8.4.31`: XSS in stringified CSS, `.map` files read through `sourceMappingURL`). They need CSS written by
  an attacker; that copy only compiles the application's own stylesheets at build time, and the application's
  pipeline (Tailwind) uses `postcss@8.5.28`, which is not affected. The only fix is Next.js 16, a major upgrade:
  after v1.0 ([POST_V1_ROADMAP.md](POST_V1_ROADMAP.md)).

## Next steps, in order

On the development machine (once):

```bash
git status                                   # only the release-closure changes
git add -A && git commit -m "Release closure: v1.0 release status, notes and roadmap"
git push
git log -1 --format=%H -- docs/V1_RELEASE_STATUS.md   # the release candidate; wait for CI to be green on it
```

On the server, as the service account:

```bash
cd ~/apps/reelforge-studio
RC=<the release candidate printed above>
./deploy.sh                                  # ends with "ReelForge deployment completed OK"
git rev-parse HEAD                           # equal to $RC
bash deploy/release-preflight.sh --expect-commit "$RC" --expect-origin https://reelforge.mul-service.com
```

Then the domain change ([below](#domain-change)), then every gate of [LIVE_VERIFICATION.md](LIVE_VERIFICATION.md),
each recorded in Admin → Verification right after it was checked, then:

```bash
bash deploy/release-report.sh                # READY_FOR_TAG, or the blockers that remain
bash deploy/release-report.sh --json > release-report-v1.0.0.json
```

## Domain change

`https://studio.imokome-cloud.com` → `https://reelforge.mul-service.com`. After `./deploy.sh` (migration
`0026_change_production_origin`), System Settings hold the new origin with Secure cookies; an installation whose
origin is anything else keeps it. From then on the API refuses requests whose `Origin` is the old domain, unless an
administrator stores it again.

Every address below is derived from the public origin and shown in the admin UI; nothing outside the repository
changes by itself, and no gate is recorded automatically. Update each system, check it, then record the gate:

| System | Setting | New value | Gate |
| --- | --- | --- | --- |
| Cloudflare Zero Trust | Tunnel → published application route | `reelforge.mul-service.com` → `http://127.0.0.1:3001`; the old hostname removed or redirected (301) to the new origin | `domain_cloudflare_route` |
| Google Cloud (YouTube) | OAuth client → Authorized redirect URIs; consent screen → Authorized domains | `https://reelforge.mul-service.com/youtube/callback`; `mul-service.com` | `domain_google_redirect` |
| TikTok for Developers | Login Kit → Redirect URI | `https://reelforge.mul-service.com/channels/callback/tiktok` | `domain_tiktok_redirect` |
| Meta for Developers | Facebook Login → Valid OAuth Redirect URIs; App domains | `https://reelforge.mul-service.com/channels/callback/facebook`; `reelforge.mul-service.com` | `domain_facebook_redirect` |
| payOS | Payment channel → Webhook URL | `https://reelforge.mul-service.com/api/webhooks/payos` | `domain_payos_webhook` |
| OnePAY | IPN URL (registered by OnePAY); return URL if they registered it | `https://reelforge.mul-service.com/api/webhooks/onepay`; `https://reelforge.mul-service.com/api/billing/onepay/return` | `domain_onepay_urls` |

Also in the repository's scope, checked by the pre-flight and readiness: no OAuth redirect override left on another
origin (Admin → System settings → Social OAuth, `/etc/reelforge/runtime.env`). Email links and the payOS / OnePAY
return pages follow the origin by themselves. Live gates already checked on the old domain (a YouTube upload, a payOS
webhook, an OnePAY IPN…) are repeated on the new one.

**Order of the switch** (the migration switches the stored origin the moment it runs; it runs on every database
`alembic upgrade head` is pointed at, so only on the server, as part of the switch):

1. Before deploying: add the Cloudflare route for `reelforge.mul-service.com` beside the old one, and **add** the new
   redirect URIs at Google, TikTok and Meta next to the old ones. Nothing changes for users yet.
2. Deploy (`./deploy.sh`): migration 0026 moves the origin. From now on sign-in works on the new domain only. (A
   server that still has the `deploy.sh` of an older commit runs that old copy for this one deployment, because bash
   keeps the file it started: run `./deploy.sh` once more so the current steps run, then the pre-flight.)
3. Point payOS and OnePAY at the new URLs. Until then their callbacks still reach the server through the old route:
   webhooks and IPNs carry no `Origin`, so the same-origin check does not refuse them.
4. Then turn the old hostname into a redirect (301) to `https://reelforge.mul-service.com` (or remove its route), and
   remove the old redirect URIs at Google, TikTok and Meta.
5. Record each `domain_*` gate, and repeat the live gates that use an outside callback.

## Legal

The Terms of Service and the Privacy Policy are templates (`frontend/src/lib/i18n/{vi,en,ja}.ts`, section `legal`).
They keep their "This is a template…" notice and the "LEGAL REVIEW REQUIRED" marker until the operator replaces them.
Nobody may record `legal_terms_reviewed` or `legal_privacy_reviewed` before every placeholder is filled in, in all
three languages, and a lawyer for the jurisdiction has reviewed the text. No final legal text is written here. While
any placeholder remains, the release stays `RELEASE_CANDIDATE`.

Placeholders found on 2026-10-03 (11, the same in each language; `TERMS_VERSION` is `2026-10-02`, the template's):

| Placeholder (English) | Vietnamese | Japanese | In |
| --- | --- | --- | --- |
| `[OPERATOR NAME]` | `[TÊN ĐƠN VỊ VẬN HÀNH]` | `[運営者名]` | Terms 1 |
| `[WEBSITE ADDRESS]` | `[ĐỊA CHỈ WEBSITE]` | `[ウェブサイトのアドレス]` | Terms 1 |
| `[REFUND POLICY]` | `[CHÍNH SÁCH HOÀN TIỀN]` | `[返金ポリシー]` | Terms 3 |
| `[LIMITATION OF LIABILITY — LEGAL REVIEW REQUIRED]` | `[ĐIỀU KHOẢN GIỚI HẠN TRÁCH NHIỆM — CẦN LUẬT SƯ RÀ SOÁT]` | `[責任の制限 — 法務の確認が必要]` | Terms 8 |
| `[GOVERNING LAW AND DISPUTE RESOLUTION]` | `[LUẬT ÁP DỤNG VÀ CƠ QUAN GIẢI QUYẾT TRANH CHẤP]` | `[準拠法および紛争解決]` | Terms 9 |
| `[CONTACT EMAIL]` | `[EMAIL LIÊN HỆ]` | `[連絡先メール]` | Terms 10, Privacy 6 and 9 |
| `[OPERATOR ADDRESS]` | `[ĐỊA CHỈ ĐƠN VỊ VẬN HÀNH]` | `[運営者の住所]` | Terms 10, Privacy 9 |
| `[LIST]` (the AI providers in use) | `[DANH SÁCH]` | `[一覧]` | Privacy 3 |
| `[NAME]` (the email provider) | `[TÊN]` | `[名称]` | Privacy 3 |
| `[RETENTION PERIOD FOR EACH KIND OF DATA]` | `[THỜI HẠN LƯU GIỮ CHO TỪNG LOẠI DỮ LIỆU]` | `[データの種類ごとの保存期間]` | Privacy 5 |
| `[APPLICABLE LAW]` | `[LUẬT ÁP DỤNG]` | `[適用法]` | Privacy 6 |

After the review: replace the placeholders in all three languages, set `TERMS_VERSION` in `app/accounts.py` to the
published version (every user is asked to accept again), commit, and run the release again from CI.

## Known limitations (after v1.0)

Accepted for v1.0, not blockers: no master-key rotation; no single sign-on; no studio deletion; account closure and
erasure handled by an administrator, by hand; expired sessions and used tokens not purged; operational alerts in the
app only; one API process by default; credits are not tied to the providers' money cost; one local media root (no
object storage). Details: [FINAL_PRODUCT_AUDIT.md § 16](FINAL_PRODUCT_AUDIT.md#16-known-limitations). What may come
next, kept apart from the release: [POST_V1_ROADMAP.md](POST_V1_ROADMAP.md). What v1.0 ships:
[RELEASE_NOTES_V1.md](RELEASE_NOTES_V1.md).

## A bug found during verification

1. Fix only that bug, and add a regression test that fails without the fix.
2. Run the suites again (SQLite, PostgreSQL, `alembic check`, frontend build, browser tests).
3. Commit the fix together with this file (what changed, in the table at the top): the new commit becomes the release
   candidate, and `git log -1 --format=%H -- docs/V1_RELEASE_STATUS.md` prints it.
4. Push, wait for CI to be green on it, then `./deploy.sh` on the server.
5. Run the pre-flight again, and repeat every manual gate the fix can affect (set them back to *Not checked* first).

## Tagging v1.0.0 (only when the report says READY_FOR_TAG)

Documented here, never run automatically. The tag goes on the exact commit that was deployed and verified (the
commit the report names), from a clean working tree, and nothing is committed between that deployment and the tag:

```bash
RELEASE=<the commit of the report: git rev-parse HEAD on the server>
git fetch origin
git status                                     # clean: nothing to commit
git rev-parse HEAD                             # equal to $RELEASE
git tag -a v1.0.0 "$RELEASE" -m "ReelForge Studio v1.0.0"
git show --no-patch v1.0.0                     # check the tagger, the date and the commit
# Pushing publishes the tag: do it deliberately, after a last look.
# git push origin v1.0.0
```

Then, in one commit that changes only this file, add to the history above the `READY_FOR_TAG` line (date, commit,
the report) and, once the tag is pushed, the `RELEASED v1.0.0` line, and set the status at the top to
**RELEASED v1.0.0**. Keep the report's JSON with the release notes. That commit is documentation only and is not part
of the tag.
