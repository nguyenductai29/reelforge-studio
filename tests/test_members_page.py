"""Settings → Members, one page at a time: GET /api/workspace/members/page.

* members first (owner, admins, editors, viewers), then pending invitations, each by email;
* search by email or display name, case-insensitive, with ``%`` and ``_`` taken literally;
* role and status filters (status ``pending``: invitations neither accepted nor revoked, expired ones flagged);
* server-side pages with the total; out-of-range parameters refused;
* invitations only for those who manage members; another studio's people never; nothing secret in the answer.
"""
import unittest

try:
    from tests.studio_harness import run_program
except ModuleNotFoundError:  # run from inside tests/
    from studio_harness import run_program


MEMBERS = r'''
from app.models import WorkspaceInvite
ORIGIN = {"Origin": "http://testserver"}

def browser():
    return TestClient(app, headers=ORIGIN)

def token(link):
    return link.split("#token=", 1)[1]

# The PRELUDE's owner invites; people join with the link (email is off, so the link comes back).
people = {}
for email, role, name in (("ada@example.com", "admin", "Ada Admin"), ("eve@example.com", "editor", None),
                          ("ed_2@example.com", "editor", "Edgar"), ("vic@example.com", "viewer", None)):
    link = client.post("/api/workspace/invites", json={"email": email, "role": role}).json()["link"]
    person = browser()
    r = person.post("/api/register", json={"email": email, "password": f"{role}-password-123", "accept_terms": True,
                                           "invite_token": token(link)})
    assert r.status_code == 201, r.text
    if name:
        assert person.put("/api/settings/profile", json={"display_name": name}).status_code == 200
    people[email] = person
# Pending invitations (one of them expired) and a revoked one, which is not listed.
for email, role in (("pending-a@example.com", "editor"), ("pending_b@example.com", "viewer"), ("late@example.com", "admin")):
    assert client.post("/api/workspace/invites", json={"email": email, "role": role}).status_code == 201
revoked = client.post("/api/workspace/invites", json={"email": "gone@example.com", "role": "viewer"}).json()["id"]
assert client.delete(f"/api/workspace/invites/{revoked}").status_code == 204
with Session.begin() as db:
    db.scalar(select(WorkspaceInvite).where(WorkspaceInvite.email == "late@example.com")).expires_at = \
        datetime.now(timezone.utc) - timedelta(days=1)
# Another studio and its invitation.
other = browser()
assert other.post("/api/register", json={"email": "stranger@example.com", "password": "stranger-password-1",
                                         "workspace_name": "Other", "accept_terms": True}).status_code == 201
assert other.post("/api/workspace/invites", json={"email": "elsewhere@example.com", "role": "editor"}).status_code == 201

def page(who=client, **params):
    r = who.get("/api/workspace/members/page", params=params)
    assert r.status_code == 200, r.text
    return r.json()

def emails(answer):
    return [item["email"] for item in answer["items"]]

everything = page()
assert (everything["total"], everything["limit"], everything["offset"]) == (8, 20, 0), everything
assert emails(everything) == ["owner@example.com", "ada@example.com", "ed_2@example.com", "eve@example.com",
                              "vic@example.com", "late@example.com", "pending-a@example.com",
                              "pending_b@example.com"], emails(everything)
owner, ada = everything["items"][0], everything["items"][1]
assert owner["kind"] == "member" and owner["role"] == "owner" and owner["you"] is True and owner["status"] == "active"
assert owner["user_id"] == owner["id"] and owner["joined_at"] and owner["account_active"] is True
assert ada["display_name"] == "Ada Admin" and ada["you"] is False and ada["role"] == "admin"
late = next(item for item in everything["items"] if item["email"] == "late@example.com")
assert late["kind"] == "invite" and late["status"] == "pending" and late["expired"] is True, late
assert late["invited_by"] == "owner@example.com" and late["invited_at"] and late["expires_at"]
assert next(item for item in everything["items"] if item["email"] == "pending-a@example.com")["expired"] is False
text = json.dumps(everything)
assert "gone@example.com" not in text and "stranger@example.com" not in text and "elsewhere@example.com" not in text
assert "token" not in text and "password" not in text

# Search: email or display name, any case; % and _ are literal characters.
assert emails(page(q="EDGAR")) == ["ed_2@example.com"]
assert emails(page(q="  Ada  ")) == ["ada@example.com"]
assert emails(page(q="pending_")) == ["pending_b@example.com"]  # not pending-a: "_" is not a wildcard
assert page(q="%")["total"] == 0
assert emails(page(q="example.com", limit=2)) == ["owner@example.com", "ada@example.com"]

# Role and status filters, alone and together.
assert emails(page(role="editor")) == ["ed_2@example.com", "eve@example.com", "pending-a@example.com"]
assert emails(page(role="owner")) == ["owner@example.com"]
active = page(status="active")
assert active["total"] == 5 and {item["kind"] for item in active["items"]} == {"member"}
pending = page(status="pending")
assert pending["total"] == 3 and {item["kind"] for item in pending["items"]} == {"invite"}
assert emails(page(role="viewer", status="pending")) == ["pending_b@example.com"]
assert page(role="owner", status="pending")["total"] == 0

# Pages of the same list, and the total with each.
first, second, last = page(limit=3), page(limit=3, offset=3), page(limit=3, offset=6)
assert first["total"] == second["total"] == last["total"] == 8
assert emails(first) + emails(second) + emails(last) == emails(everything)
assert page(limit=3, offset=9)["items"] == []
for params in ({"limit": 0}, {"limit": 101}, {"offset": -1}, {"role": "boss"}, {"status": "expired"}, {"q": "x" * 256}):
    assert client.get("/api/workspace/members/page", params=params).status_code == 422, params

# A viewer sees the members, never the invitations; an admin, who manages members, sees both.
viewer = page(people["vic@example.com"])
assert viewer["total"] == 5 and {item["kind"] for item in viewer["items"]} == {"member"}
assert next(item for item in viewer["items"] if item["email"] == "vic@example.com")["you"] is True
assert page(people["vic@example.com"], status="pending")["total"] == 0
assert page(people["eve@example.com"])["total"] == 5
assert page(people["ada@example.com"])["total"] == 8
# The other studio's owner sees only their own studio.
assert emails(page(other)) == ["stranger@example.com", "elsewhere@example.com"]
assert TestClient(app).get("/api/workspace/members/page").status_code == 401
print("members page ok")
'''


class MembersPageTest(unittest.TestCase):
    def test_search_filters_pages_and_visibility(self):
        result = run_program(MEMBERS)
        self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-6000:])
        self.assertIn("members page ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
