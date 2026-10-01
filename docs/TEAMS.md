# Studios, members and roles (v1.0)

A studio (workspace) holds projects, workflows, media, channels, settings, credits and a subscription. People work in
it as members with a role. Every API request resolves one studio and checks the member's role **on the server**; the
interface only hides what a role cannot use.

## Roles

| Permission | Owner | Admin | Editor | Viewer |
| --- | :---: | :---: | :---: | :---: |
| See the studio (projects, workflows, media, runs, the plan) | ✓ | ✓ | ✓ | ✓ |
| Create and edit content (projects, workflows, media) | ✓ | ✓ | ✓ | |
| Run workflows | ✓ | ✓ | ✓ | |
| Publish to channels | ✓ | ✓ | ✓ ¹ | |
| Connect and disconnect channels | ✓ | ✓ | | |
| Default models, studio settings | ✓ | ✓ | | |
| Invite, change roles, remove members | ✓ | ✓ ² | | |
| See the payment history | ✓ | ✓ | | |
| Buy or renew a plan | ✓ | | | |
| Transfer ownership | ✓ | | | |

1. Unless the owner or an admin turns off *Editors may publish* (Settings → Members).
2. An admin manages admins, editors and viewers, never the owner.

The matrix is `app/permissions.py`; endpoints call `workspace_for(request, db, "<permission>")`, which answers 403
when the member's role lacks it. Each member also has their own sessions, 2FA and notifications: those belong to the
account, not to a studio.

## Which studio a request uses

1. The studio chosen in this session (`login_sessions.active_workspace_id`), set by the switcher in the top bar
   (`POST /api/workspaces/{id}/switch`);
2. otherwise the last studio the account used (`users.last_workspace_id`);
3. otherwise its oldest membership.

The membership is checked on every request, so a removed member is refused at once, and records of another studio
answer 404 (projects, workflows, runs, assets, publications, orders, tickets). An account that belongs to no studio
any more sees a page offering to create one (while registration is open) or to sign out.

## Invitations

* Owners and admins invite by email with a role (admin, editor or viewer). The invitation is valid **7 days**, works
  **once**, and its token is stored as a SHA-256 digest. It can be resent (a new link) or cancelled.
* The invitation is emailed. While email is not set up, the inviter gets the link to send another way.
* Inviting needs a verified email address (while email works) and is limited to 30 invitations a day per studio.
* `/invite#token=…` shows the studio, the role and who invited. Then:
  * **an existing account** signs in (with its 2FA code if it has one) and joins; the invitation must match its email;
  * **a new person** chooses a password and accepts the Terms, which creates the account and the membership; no Trial
    studio of their own is created.
* Owners and admins are notified when someone joins.

## Leaving, removing, transferring

* A member can leave a studio. The owner cannot: transfer the ownership first.
* Removing a member takes effect on their next request. The owner cannot be removed, so a studio always has one.
* **Ownership transfer:** the owner enters their password (and a 2FA code when 2FA is on), picks an existing member and
  types the studio name to confirm. The member becomes the owner, the previous owner stays as an admin, and the new
  owner is notified. There is exactly one owner at every moment.
* An account owns at most one studio. A member without one can create their own from the switcher (*Create my own
  studio*) while registration is open.
* Deleting a studio is not part of v1.0: there is no safe way yet to settle its subscription, credits, media and
  publications, so it is left out rather than done unsafely. An administrator can deactivate an account.

## Billing

The plan, its limits and the credits are visible to every member. The payment history is visible to owners and admins,
and only the owner buys or renews (the checkout and the manual transfer report check `billing.manage`). Payment emails
(receipt, failure, plan activated) and payment notifications go to the owner.

## Audit

Invitations sent, resent, cancelled and accepted, role changes, removals, departures, ownership transfers, studio
creation and renaming are recorded in the audit log ([SECURITY.md](SECURITY.md#audit-log)).
