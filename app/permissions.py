"""Workspace roles and what each may do (Phase 23). The API enforces these on every request.

| Permission          | Owner | Admin | Editor | Viewer | Covers |
| ------------------- | ----- | ----- | ------ | ------ | ------ |
| workspace.view      | yes   | yes   | yes    | yes    | reading projects, workflows, runs, media, publications, usage |
| content.edit        | yes   | yes   | yes    | –      | projects, workflows, scripts, uploads, media edits and deletes |
| runs.execute        | yes   | yes   | yes    | –      | starting, retrying and approving workflow runs |
| publish             | yes   | yes   | yes*   | –      | creating, scheduling, retrying, cancelling publications |
| channels.manage     | yes   | yes   | –      | –      | connecting and disconnecting YouTube, TikTok, Facebook |
| models.manage       | yes   | yes   | –      | –      | AI models of the workspace, default models |
| settings.manage     | yes   | yes   | –      | –      | workspace name and defaults, storage cleanup |
| members.manage      | yes   | yes   | –      | –      | inviting, changing roles, removing members (never the owner) |
| billing.view        | yes   | yes   | –      | –      | payment orders and their history |
| billing.manage      | yes   | –     | –      | –      | buying or renewing a plan, reporting a transfer |
| ownership.transfer  | yes   | –     | –      | –      | handing the workspace to another member |
| movie_sources.delete| yes   | yes   | –      | –      | deleting a movie source now (members add, use and extend them) |

``*`` Editors publish unless the workspace turns ``editors_can_publish`` off.
Everyone may open support tickets and read the plan, credits and storage of the workspace.
A system admin (``users.is_admin``) is a member like any other inside a workspace; the
admin console is separate.
"""

ROLES = ("owner", "admin", "editor", "viewer")
INVITABLE_ROLES = ("admin", "editor", "viewer")

PERMISSIONS: dict[str, frozenset[str]] = {
    "workspace.view": frozenset(ROLES),
    "content.edit": frozenset({"owner", "admin", "editor"}),
    "runs.execute": frozenset({"owner", "admin", "editor"}),
    "publish": frozenset({"owner", "admin", "editor"}),
    "channels.manage": frozenset({"owner", "admin"}),
    "models.manage": frozenset({"owner", "admin"}),
    "settings.manage": frozenset({"owner", "admin"}),
    "members.manage": frozenset({"owner", "admin"}),
    "billing.view": frozenset({"owner", "admin"}),
    "billing.manage": frozenset({"owner"}),
    "ownership.transfer": frozenset({"owner"}),
    "movie_sources.delete": frozenset({"owner", "admin"}),
}


def allowed(role: str | None, permission: str, *, editors_can_publish: bool = True) -> bool:
    if role not in ROLES:
        return False
    if permission == "publish" and role == "editor" and not editors_can_publish:
        return False
    return role in PERMISSIONS[permission]


def granted(role: str | None, *, editors_can_publish: bool = True) -> list[str]:
    """Every permission of a role, for the frontend (which hides what the API would refuse anyway)."""
    return [name for name in PERMISSIONS if allowed(role, name, editors_can_publish=editors_can_publish)]


def can_manage_member(actor_role: str, target_role: str) -> bool:
    """Owners manage everyone but themselves through transfer; admins manage admins, editors and viewers."""
    if actor_role == "owner":
        return target_role != "owner"
    if actor_role == "admin":
        return target_role in INVITABLE_ROLES
    return False
