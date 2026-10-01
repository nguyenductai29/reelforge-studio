"""Phase 23: teams, roles, invitations, workspace switching and ownership transfer.

* the permission matrix (app/permissions.py), enforced by the API, never by hiding buttons;
* invitations: hashed token, expiry, single use, the invited address only, revocable, resent with a new link,
  shareable by hand when email is off; a new account created from an invitation joins without a studio of its own,
  even with self-registration closed;
* roles: owner, admin, editor, viewer; editors publish unless the workspace turns it off;
* switching: an explicit active workspace per session, remembered for new sessions; data of another workspace is
  never reachable; a removed member loses access on the next request;
* ownership transfer: password and the workspace name to confirm, exactly one owner before and after;
* every change audited.
"""
import unittest

try:
    from tests.studio_harness import run_program
except ModuleNotFoundError:  # run from inside tests/
    from studio_harness import run_program

from app import permissions


class PermissionMatrixTest(unittest.TestCase):
    def test_matrix(self):
        expected = {
            "owner": set(permissions.PERMISSIONS),
            "admin": set(permissions.PERMISSIONS) - {"billing.manage", "ownership.transfer"},
            "editor": {"workspace.view", "content.edit", "runs.execute", "publish"},
            "viewer": {"workspace.view"},
        }
        for role, granted in expected.items():
            with self.subTest(role=role):
                self.assertEqual(set(permissions.granted(role)), granted)
        self.assertNotIn("publish", permissions.granted("editor", editors_can_publish=False))
        self.assertIn("publish", permissions.granted("admin", editors_can_publish=False))
        self.assertEqual(permissions.granted("stranger"), [])
        self.assertTrue(permissions.can_manage_member("owner", "admin"))
        self.assertFalse(permissions.can_manage_member("owner", "owner"))
        self.assertTrue(permissions.can_manage_member("admin", "admin"))
        self.assertFalse(permissions.can_manage_member("admin", "owner"))
        self.assertFalse(permissions.can_manage_member("editor", "viewer"))


TEAM = r'''
from app import team
from app.models import AuditEvent, Membership, Notification, SystemSetting, User, Workspace, WorkspaceInvite
ORIGIN = {"Origin": "http://testserver"}

def browser():
    return TestClient(app, headers=ORIGIN)

def token(link):
    return link.split("#token=", 1)[1]

def invite(c, email, role):
    r = c.post("/api/workspace/invites", json={"email": email, "role": role})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["emailed"] is False and "/invite#token=" in body["link"], body  # email off: share by hand
    return body

def registration(enabled):
    with Session.begin() as db:
        db.get(SystemSetting, "registration_enabled").value = json.dumps(enabled)

def user_id(email):
    with Session() as db:
        return db.scalar(select(User.id).where(User.email == email))

dash = client.get("/api/dashboard").json()
W1 = dash["workspace"]["id"]
assert dash["workspace"]["role"] == "owner" and "ownership.transfer" in dash["permissions"]
assert dash["workspaces"] == [{"id": W1, "name": "My Studio", "role": "owner", "active": True}]

# --- invitations ----------------------------------------------------------------------------------------------------
editor_invite = invite(client, "Editor@Example.com", "editor")
viewer_invite = invite(client, "viewer@example.com", "viewer")
admin_invite = invite(client, "teamadmin@example.com", "admin")
assert client.post("/api/workspace/invites", json={"email": "x@example.com", "role": "owner"}).status_code == 422
assert client.post("/api/workspace/invites", json={"email": "not-an-email", "role": "viewer"}).status_code == 422
assert client.post("/api/workspace/invites", json={"email": "owner@example.com", "role": "viewer"}).json()["code"] == "already_member"
with Session() as db:
    stored = db.scalars(select(WorkspaceInvite)).all()
    assert all(len(row.token_hash) == 64 and row.token_hash not in (editor_invite["link"], viewer_invite["link"])
               for row in stored)
anon = browser()
info = anon.post("/api/invites/lookup", json={"token": token(editor_invite["link"])}).json()
assert info["status"] == "pending" and info["email"] == "editor@example.com" and info["role"] == "editor"
assert info["workspace"] == "My Studio" and info["invited_by"] == "owner@example.com" and info["account_exists"] is False
assert anon.post("/api/invites/lookup", json={"token": "x" * 30}).json() == {"status": "invalid"}

# A new account from an invitation joins the workspace (no studio of its own), even with registration closed.
registration(False)
assert browser().post("/api/register", json={"email": "z@example.com", "password": "z-password-1234",
                                              "workspace_name": "Z", "accept_terms": True}).status_code == 403
assert browser().post("/api/register", json={"email": "someone@example.com", "password": "some-password-12",
                                              "accept_terms": True, "invite_token": token(viewer_invite["link"])}).status_code == 403
editor = browser()
r = editor.post("/api/register", json={"email": "editor@example.com", "password": "editor-password-1", "accept_terms": True,
                                       "invite_token": token(editor_invite["link"])})
assert r.status_code == 201, r.text
d = editor.get("/api/dashboard").json()
assert d["workspace"]["id"] == W1 and d["workspace"]["role"] == "editor" and len(d["workspaces"]) == 1, d
assert d["account"]["email_verified"] is True  # the invitation reached this address
assert set(d["permissions"]) == {"workspace.view", "content.edit", "runs.execute", "publish"}
assert anon.post("/api/invites/lookup", json={"token": token(editor_invite["link"])}).json()["status"] == "accepted"
assert browser().post("/api/register", json={"email": "editor2@example.com", "password": "editor-password-2", "accept_terms": True,
                                              "invite_token": token(editor_invite["link"])}).status_code == 410
registration(True)

# An existing account accepts while signed in; it keeps its own studio and switches to the new one.
viewer = browser()
assert viewer.post("/api/register", json={"email": "viewer@example.com", "password": "viewer-password-1",
                                          "workspace_name": "Viewer Studio", "accept_terms": True}).status_code == 201
OWN = viewer.get("/api/dashboard").json()["workspace"]["id"]
assert editor.post("/api/invites/accept", json={"token": token(viewer_invite["link"])}).json()["code"] == "invite_email_mismatch"
r = viewer.post("/api/invites/accept", json={"token": token(viewer_invite["link"])})
assert r.status_code == 200 and r.json() == {"workspace": {"id": W1, "name": "My Studio"}, "role": "viewer"}, r.text
assert viewer.post("/api/invites/accept", json={"token": token(viewer_invite["link"])}).status_code == 410
d = viewer.get("/api/dashboard").json()
assert d["workspace"]["id"] == W1 and d["workspace"]["role"] == "viewer" and len(d["workspaces"]) == 2
assert d["permissions"] == ["workspace.view"]
teamadmin = browser()
assert teamadmin.post("/api/register", json={"email": "teamadmin@example.com", "password": "admin-password-12",
                                             "accept_terms": True, "invite_token": token(admin_invite["link"])}).status_code == 201

# --- roles are enforced by the API --------------------------------------------------------------------------------------
P1 = client.post("/api/projects", json={"title": "Owner project", "topic": "x"}).json()["id"]
assert any(p["id"] == P1 for p in viewer.get("/api/dashboard").json()["projects"])
refused = [
    ("post", "/api/projects", {"title": "Nope"}), ("patch", f"/api/projects/{P1}", {"title": "Nope"}),
    ("post", "/api/workflows", {"name": "Nope"}), ("post", "/api/ai-tools", {"task": "video", "provider": "fal", "model": "m"}),
    ("post", "/api/workspace/invites", {"email": "a@example.com", "role": "viewer"}), ("put", "/api/workspace", {"name": "X"}),
    ("post", "/api/billing/orders/none/transferred", None), ("get", "/api/billing/orders", None),
    ("post", "/api/storage/cleanup", {"apply": True}),
]
for method, path, body in refused:
    response = getattr(viewer, method)(path, **({"json": body} if body is not None else {}))
    assert response.status_code == 403, (method, path, response.status_code, response.text)
assert viewer.post("/api/support/tickets", json={"subject": "Help", "category": "other", "description": "Question"}).status_code == 201
r = editor.post("/api/projects", json={"title": "Editor project", "topic": "y"})
assert r.status_code == 201, r.text
assert editor.patch(f"/api/projects/{P1}", json={"title": "Renamed by editor"}).status_code == 200
assert editor.post("/api/workflows", json={"name": "Editor flow"}).status_code == 201
for method, path, body in (("post", "/api/ai-tools", {"task": "video", "provider": "fal", "model": "m"}),
                           ("get", "/api/channels/youtube/authorization", None),
                           ("post", "/api/workspace/invites", {"email": "a@example.com", "role": "viewer"}),
                           ("post", "/api/billing/orders/none/transferred", None), ("get", "/api/billing/orders", None),
                           ("put", "/api/workspace", {"name": "X"})):
    response = getattr(editor, method)(path, **({"json": body} if body is not None else {}))
    assert response.status_code == 403, (method, path, response.status_code, response.text)
# Editors publish unless the workspace says otherwise (an unknown publication: 409 once allowed).
assert editor.post("/api/publications/none/retry").status_code == 409
settings = client.get("/api/settings").json()["workspace"]
assert settings["editors_can_publish"] is True
assert client.put("/api/settings/workspace", json={**settings, "editors_can_publish": False}).status_code == 200
r = editor.post("/api/publications/none/retry")
assert r.status_code == 403 and r.json()["detail"] == "Your role cannot publish in this workspace", r.text
assert "publish" not in editor.get("/api/dashboard").json()["permissions"]
assert client.post("/api/publications/none/retry").status_code == 409
assert client.put("/api/settings/workspace", json={**settings, "editors_can_publish": True}).status_code == 200
assert editor.put("/api/settings/workspace", json=settings).status_code == 403

# Admins manage members but not the owner, billing or ownership.
TA = user_id("teamadmin@example.com")
EDITOR, VIEWER = user_id("editor@example.com"), user_id("viewer@example.com")
OWNER = user_id("owner@example.com")
assert teamadmin.get("/api/billing/orders").status_code == 200
assert teamadmin.post("/api/billing/orders/none/transferred").status_code == 403
assert teamadmin.put(f"/api/workspace/members/{VIEWER}", json={"role": "editor"}).json() == {"user_id": VIEWER, "role": "editor"}
assert teamadmin.put(f"/api/workspace/members/{VIEWER}", json={"role": "viewer"}).status_code == 200
assert teamadmin.put(f"/api/workspace/members/{OWNER}", json={"role": "viewer"}).json()["code"] == "cannot_manage_member"
assert teamadmin.put(f"/api/workspace/members/{TA}", json={"role": "editor"}).json()["code"] == "cannot_change_own_role"
assert teamadmin.delete(f"/api/workspace/members/{OWNER}").json()["code"] == "cannot_remove_owner"
assert teamadmin.post("/api/workspace/transfer", json={"user_id": TA, "password": "admin-password-12",
                                                       "confirm": "My Studio"}).status_code == 403
assert teamadmin.put("/api/workspace", json={"name": "My  Studio "}).json()["name"] == "My Studio"
members = client.get("/api/workspace/members").json()
assert [m["role"] for m in members["members"]] == ["owner", "admin", "editor", "viewer"], members["members"]
assert members["can_manage"] and members["can_transfer"] and [i["email"] for i in members["invites"]] == []
mine = viewer.get("/api/workspace/members").json()
assert mine["can_manage"] is False and mine["invites"] == [] and mine["role"] == "viewer"

# --- switching: an explicit workspace per session, nothing from the other one ------------------------------------------------
assert viewer.post(f"/api/workspaces/{OWN}/switch").json()["id"] == OWN
d = viewer.get("/api/dashboard").json()
assert d["workspace"]["id"] == OWN and d["workspace"]["role"] == "owner" and all(p["id"] != P1 for p in d["projects"])
assert viewer.patch(f"/api/projects/{P1}", json={"title": "Cross"}).status_code == 404
assert viewer.post("/api/workspaces/not-a-workspace/switch").status_code == 404
assert viewer.post(f"/api/workspaces/{W1}/switch").status_code == 200
again = browser()
assert again.post("/api/login", json={"email": "viewer@example.com", "password": "viewer-password-1"}).status_code == 200
assert again.get("/api/dashboard").json()["workspace"]["id"] == W1  # remembered for new sessions
assert again.post(f"/api/workspaces/{OWN}/switch").status_code == 200
assert viewer.get("/api/dashboard").json()["workspace"]["id"] == W1  # each session has its own

# --- removal takes effect on the next request; leaving; the owner cannot leave ------------------------------------------------
assert client.delete(f"/api/workspace/members/{VIEWER}").status_code == 204
d = viewer.get("/api/dashboard").json()
assert d["workspace"]["id"] == OWN and len(d["workspaces"]) == 1, d
assert viewer.patch(f"/api/projects/{P1}", json={"title": "After removal"}).status_code == 404
assert viewer.get("/api/notifications").json()["items"][0]["type"] == "team.removed"
assert client.delete(f"/api/workspace/members/{EDITOR}").status_code == 204
r = editor.get("/api/dashboard")
assert r.status_code == 403 and r.json()["detail"] == "No workspace", r.text
assert client.post("/api/workspace/leave").json()["code"] == "owner_cannot_leave"

# --- ownership transfer -----------------------------------------------------------------------------------------------------------
heir_invite = invite(client, "heir@example.com", "editor")
heir = browser()
assert heir.post("/api/register", json={"email": "heir@example.com", "password": "heir-password-123", "accept_terms": True,
                                        "invite_token": token(heir_invite["link"])}).status_code == 201
HEIR = user_id("heir@example.com")
transfer = {"user_id": HEIR, "password": "long-password-123", "confirm": "My Studio"}
assert client.post("/api/workspace/transfer", json={**transfer, "password": "wrong-password-0"}).status_code == 400
assert client.post("/api/workspace/transfer", json={**transfer, "confirm": "Other"}).status_code == 400
assert client.post("/api/workspace/transfer", json={**transfer, "user_id": VIEWER}).json()["code"] == "member_not_found"
r = client.post("/api/workspace/transfer", json=transfer)
assert r.status_code == 200 and r.json() == {"owner_id": HEIR, "your_role": "admin"}, r.text
with Session() as db:
    assert db.get(Workspace, W1).owner_id == HEIR and team.owner_count(db, W1) == 1
    assert db.get(Membership, (OWNER, W1)).role == "admin"
d = heir.get("/api/dashboard").json()
assert d["workspace"]["role"] == "owner" and "billing.manage" in d["permissions"]
assert client.post("/api/billing/orders/none/transferred").status_code == 403  # no longer the owner
assert client.post("/api/workspace/transfer", json={**transfer, "user_id": OWNER}).status_code == 403
assert teamadmin.post("/api/workspace/leave").json() == {"ok": True}
assert teamadmin.get("/api/dashboard").status_code == 403

# --- invitations: revoked, expired, resent ------------------------------------------------------------------------------------------
late = invite(heir, "late@example.com", "viewer")
with Session() as db:
    late_id = db.scalar(select(WorkspaceInvite.id).where(WorkspaceInvite.email == "late@example.com"))
assert heir.delete(f"/api/workspace/invites/{late_id}").status_code == 204
assert anon.post("/api/invites/lookup", json={"token": token(late["link"])}).json()["status"] == "revoked"
assert browser().post("/api/register", json={"email": "late@example.com", "password": "late-password-123", "accept_terms": True,
                                              "invite_token": token(late["link"])}).status_code == 410
soon = invite(heir, "soon@example.com", "viewer")
resent = heir.post(f"/api/workspace/invites/{heir.get('/api/workspace/members').json()['invites'][0]['id']}/resend").json()
assert anon.post("/api/invites/lookup", json={"token": token(soon["link"])}).json() == {"status": "invalid"}
assert anon.post("/api/invites/lookup", json={"token": token(resent["link"])}).json()["status"] == "pending"
with Session.begin() as db:
    db.execute(update(WorkspaceInvite).where(WorkspaceInvite.email == "soon@example.com")
               .values(expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
assert anon.post("/api/invites/lookup", json={"token": token(resent["link"])}).json()["status"] == "expired"

with Session() as db:
    actions = {row.action for row in db.scalars(select(AuditEvent))}
for expected in ("workspace.member_invited", "workspace.invite_accepted", "workspace.member_role_changed",
                 "workspace.member_removed", "workspace.member_left", "workspace.ownership_transferred",
                 "workspace.switched", "workspace.invite_revoked", "workspace.renamed"):
    assert expected in actions, expected
print("ok")
'''


class TeamTest(unittest.TestCase):
    def test_invitations_roles_switching_removal_and_transfer(self):
        result = run_program(TEAM)
        self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-6000:])
        self.assertTrue(result.stdout.strip().endswith("ok"), result.stdout[-2000:])


if __name__ == "__main__":
    unittest.main()
