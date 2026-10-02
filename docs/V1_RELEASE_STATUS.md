# ReelForge Studio v1.0 — release status

> **Status: RELEASE_CANDIDATE.** Not ready for the `v1.0.0` tag: CI has not passed on the release candidate, and no
> production gate has been recorded yet. This file says so until every mandatory gate passes; it is updated by the
> operator, from what was actually checked.

| | |
| --- | --- |
| Release candidate | The commit that contains the release-closure changes on `feat/studio-foundation` (it follows `08f1eef9` "ADD phase 27"). Write its hash here once it is committed: `________` |
| Database head | `0025_verification_status` (the release closure added it: a status on each verification gate; nullable column, reversible) |
| Deployed on the server | Not yet. Fill in: date, commit, by whom (`./deploy.sh` output). The first deploy of this release still runs the previous `deploy.sh` (bash keeps the file it started); run `./deploy.sh` once more so the new steps run, then the pre-flight |
| CI on the release candidate | **FAIL** on `08f1eef9`: GitHub Actions has not been green on this branch. A Linux-only test failure was fixed and failing tests now appear as annotations on the run page; CI must run again on the new commit |
| Report | `bash deploy/release-report.sh` on the server. Keep its `--json` output with the release notes |

## How the release is decided

The gates are listed in [RELEASE_V1_CHECKLIST.md](RELEASE_V1_CHECKLIST.md); each is recorded in one place: the
pre-flight (`bash deploy/release-preflight.sh`), CI, or Admin → Verification (by the person who checked it,
[LIVE_VERIFICATION.md](LIVE_VERIFICATION.md)). The release may be tagged only when **all** of these hold on the
deployed commit:

1. CI is green on that exact commit (backend on SQLite with Python 3.11 and 3.14, backend on PostgreSQL, migrations,
   frontend on Node 20 and 22, Playwright). A local run never replaces it.
2. The pre-flight has no FAIL (`--expect-commit <commit>`).
3. Every gate in Admin → Verification is *Passed*, or *Not applicable* where that is allowed (payOS, OnePAY, Runway,
   TikTok, Facebook, when the installation does not use them).
4. `bash deploy/release-report.sh` says `READY_FOR_TAG`.

Nothing passes by itself: no tool records a gate, and a gate nobody recorded counts as not passed.

## Status by area

PASS, FAIL, MANUAL (not yet checked by a person on the production server) or NOT_APPLICABLE. Update a row only from
a real check, with the date and who.

| Area | Status | Evidence / what remains | Date / by |
| --- | --- | --- | --- |
| CI on the release candidate | FAIL | `08f1eef9` failed on GitHub Actions; re-run on the new commit (`release_ci_green`) | 2026-10-02 |
| Automated suites, development machine | PASS | See [RELEASE_V1_CHECKLIST.md](RELEASE_V1_CHECKLIST.md#automated-evidence-development-machine-2026-10-02-release-closure). Evidence only: not a gate | 2026-10-02 |
| Deployment (`./deploy.sh`) | MANUAL | `release_deploy` | |
| Pre-flight on the server | MANUAL | `release_preflight`: source, database and head, master key, services and timers, ports, health, storage, backups, FFmpeg, configuration, administrators | |
| Platform | MANUAL | `migration_upgraded`, `storage_on_hdd`, `ffmpeg_verified`, `master_key_file` | |
| Email | MANUAL | E1–E7: test email, SPF/DKIM/DMARC, verification, reset, password changed, invitation, support reply | |
| AI providers | MANUAL | Gemini text, Gemini TTS, transcription; Runway image and video (or NOT_APPLICABLE); credits once and the refund path | |
| Render | MANUAL | Voice, subtitles and music; slideshow; Movie Recap; Movie Review | |
| Payments: manual VietQR | MANUAL | `bank_qr_round_trip` (mandatory) | |
| Payments: payOS | MANUAL | Or NOT_APPLICABLE if payOS is not used | |
| Payments: OnePAY | MANUAL | Sandbox, then production; or NOT_APPLICABLE if cards are not offered | |
| Publishing | MANUAL | YouTube private, scheduled post; TikTok and Facebook (or NOT_APPLICABLE) | |
| Cloudflare and security | MANUAL | HTTPS and headers, cookie flags, foreign Origin, real and forged client addresses, 2FA, sessions, rate limits, SSE through Cloudflare | |
| Backups and recovery rehearsal | MANUAL | Timer and dump, off-server copies of dump and key, rehearsal into `reelforge_restore_test` | |
| Media backup | MANUAL | Manifest, copy, verify | |
| Operations | MANUAL | Alerts, support and account closure, maintenance timer, cleanup dry run | |
| Reboot | MANUAL | `server_reboot` | |
| Legal | MANUAL | Placeholders below still in the templates; lawyer review pending | |

Accepted pre-flight warnings (write each one and why): none yet.

## Legal

The Terms of Service and the Privacy Policy are templates (`frontend/src/lib/i18n/{vi,en,ja}.ts`, section `legal`).
They keep their "This is a template…" notice and the "LEGAL REVIEW REQUIRED" marker until the operator replaces them.
Nobody may record `legal_terms_reviewed` or `legal_privacy_reviewed` before every placeholder is filled in, in all
three languages, and a lawyer for the jurisdiction has reviewed the text. No final legal text is written here.

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

## A bug found during verification

1. Fix only that bug, and add a regression test that fails without the fix.
2. Run the suites again (SQLite, PostgreSQL, `alembic check`, frontend build, browser tests).
3. Commit: the new commit becomes the release candidate. Write it at the top of this file.
4. Push, wait for CI to be green on it, then `./deploy.sh` on the server.
5. Run the pre-flight again, and repeat every manual gate the fix can affect (set them back to *Not checked* first).

## Tagging v1.0.0 (only when the report says READY_FOR_TAG)

Documented here, never run automatically. The tag goes on the exact commit that was deployed and verified:

```bash
git fetch origin
git rev-parse <release commit>                 # equal to the commit above and to `git rev-parse HEAD` on the server
git tag -a v1.0.0 <release commit> -m "ReelForge Studio v1.0.0"
git show --no-patch v1.0.0                     # check the tagger, the date and the commit
# Pushing publishes the tag: do it deliberately, after a last look.
# git push origin v1.0.0
```

Then set the status at the top of this file to **RELEASED v1.0.0** with the date, and keep the report's JSON with
the release notes.
