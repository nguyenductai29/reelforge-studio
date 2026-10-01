"""Teams (Phase 23): members and roles, invitations, the active workspace, ownership transfer.

**Active workspace.** A user can belong to several workspaces. Each session works in one:
``login_sessions.active_workspace_id``, set by switching. Without one (a new session),
the API uses the one the user last switched to (``users.last_workspace_id``), else the
oldest membership. Every request re-checks the membership, so removing a member takes
effect on their very next request; a session pointing at a workspace the user left falls
back to another one, never to someone else's data.

**Roles** are owner, admin, editor and viewer (``app/permissions.py``). Every workspace
has exactly one owner: owners are created with the workspace and changed only by
``transfer``. The owner cannot be removed or demoted and cannot leave.

**Invitations** go to an email address with a role (admin, editor or viewer). The token is
random (256 bits); only its SHA-256 is stored. It expires after 7 days and is accepted
once, by a signed-in account with that email address (so accepting also proves the
address). A new invitation to the same address replaces the previous one.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import secrets
import uuid

from sqlalchemy import func, inspect, select, update

from app import permissions
from app.models import (LoginSession, Membership, User, UserProfile, Workspace, WorkspaceInvite, WorkspaceSetting)

INVITE_LIFETIME = timedelta(days=7)
EMAIL_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
ROLE_ORDER = {role: index for index, role in enumerate(permissions.ROLES)}


class TeamError(Exception):
    """A refused team change: a stable ``code`` and the HTTP status it maps to."""

    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.code = code
        self.status = status


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _iso(value: datetime | None) -> str | None:
    value = _aware(value)
    return value.isoformat() if value else None


def _digest(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# --- the active workspace ----------------------------------------------------------------------------------------

_ready = {"value": False}


def ready(db) -> bool:
    """Whether migration 0023 is applied (an upgrade in progress uses the oldest membership, as before it)."""
    if not _ready["value"]:
        _ready["value"] = "active_workspace_id" in {column["name"] for column in
                                                    inspect(db.connection()).get_columns("login_sessions")}
    return _ready["value"]


def memberships(db, user_id: str) -> list[tuple[Membership, Workspace]]:
    return list(db.execute(select(Membership, Workspace).join(Workspace, Workspace.id == Membership.workspace_id)
                           .where(Membership.user_id == user_id)
                           .order_by(Workspace.created_at, Workspace.id)).all())


def active(db, user: User, session: LoginSession | None) -> tuple[Membership, Workspace] | None:
    """The membership this session works in (see the module docstring), or None without any."""
    candidates = (session.active_workspace_id if session else None, user.last_workspace_id) if ready(db) else ()
    for workspace_id in candidates:
        if workspace_id:
            membership = db.get(Membership, (user.id, workspace_id))
            if membership is not None:
                return membership, db.get(Workspace, workspace_id)
    found = memberships(db, user.id)
    return found[0] if found else None


def switch(db, user: User, session: LoginSession, workspace_id: str) -> Workspace:
    membership = db.get(Membership, (user.id, workspace_id))
    if membership is None:
        raise TeamError("not_a_member", 404)
    session.active_workspace_id = workspace_id
    user.last_workspace_id = workspace_id
    return db.get(Workspace, workspace_id)


def workspace_flag(db, workspace_id: str, key: str, default: bool) -> bool:
    row = db.get(WorkspaceSetting, (workspace_id, key))
    if row is None:
        return default
    try:
        return bool(json.loads(row.value))
    except ValueError:
        return default


def editors_can_publish(db, workspace_id: str) -> bool:
    return workspace_flag(db, workspace_id, "editors_can_publish", True)


def list_workspaces(db, user_id: str, active_id: str | None) -> list[dict]:
    return [{"id": workspace.id, "name": workspace.name, "role": membership.role, "active": workspace.id == active_id}
            for membership, workspace in memberships(db, user_id)]


# --- members ------------------------------------------------------------------------------------------------------

def member_list(db, workspace_id: str, me: str) -> list[dict]:
    rows = db.execute(select(Membership, User, UserProfile.display_name).join(User, User.id == Membership.user_id)
                      .outerjoin(UserProfile, UserProfile.user_id == User.id)
                      .where(Membership.workspace_id == workspace_id)).all()
    members = [{"user_id": user.id, "email": user.email, "display_name": name, "role": membership.role,
                "joined_at": _iso(membership.created_at), "active": user.is_active, "you": user.id == me,
                "two_factor": bool(user.totp_enabled_at), "email_verified": user.email_verified_at is not None}
               for membership, user, name in rows]
    return sorted(members, key=lambda item: (ROLE_ORDER.get(item["role"], 9), item["email"]))


def pending_invites(db, workspace_id: str) -> list[dict]:
    rows = db.execute(select(WorkspaceInvite, User.email).outerjoin(User, User.id == WorkspaceInvite.invited_by_user_id)
                      .where(WorkspaceInvite.workspace_id == workspace_id, WorkspaceInvite.accepted_at.is_(None),
                             WorkspaceInvite.revoked_at.is_(None))
                      .order_by(WorkspaceInvite.created_at.desc())).all()
    return [{"id": invite.id, "email": invite.email, "role": invite.role, "invited_by": inviter,
             "created_at": _iso(invite.created_at), "expires_at": _iso(invite.expires_at),
             "expired": _aware(invite.expires_at) <= _now()} for invite, inviter in rows]


def _member(db, workspace_id: str, user_id: str) -> Membership:
    membership = db.get(Membership, (user_id, workspace_id))
    if membership is None:
        raise TeamError("member_not_found", 404)
    return membership


def clear_active(db, user_id: str, workspace_id: str) -> None:
    """Sessions and the remembered workspace of a user who left ``workspace_id`` stop pointing at it."""
    db.execute(update(LoginSession).where(LoginSession.user_id == user_id,
                                          LoginSession.active_workspace_id == workspace_id)
               .values(active_workspace_id=None))
    db.execute(update(User).where(User.id == user_id, User.last_workspace_id == workspace_id)
               .values(last_workspace_id=None))


def change_role(db, workspace: Workspace, actor: Membership, user_id: str, role: str) -> Membership:
    if role not in permissions.INVITABLE_ROLES:
        raise TeamError("invalid_role", 422)
    target = _member(db, workspace.id, user_id)
    if target.user_id == actor.user_id:
        raise TeamError("cannot_change_own_role", 409)
    if not permissions.can_manage_member(actor.role, target.role):
        raise TeamError("cannot_manage_member", 403)
    target.role = role
    return target


def remove(db, workspace: Workspace, actor: Membership, user_id: str) -> Membership:
    target = _member(db, workspace.id, user_id)
    if target.user_id == actor.user_id:
        raise TeamError("use_leave", 409)
    if target.role == "owner":
        raise TeamError("cannot_remove_owner", 409)
    if not permissions.can_manage_member(actor.role, target.role):
        raise TeamError("cannot_manage_member", 403)
    db.delete(target)
    clear_active(db, user_id, workspace.id)
    return target


def leave(db, workspace: Workspace, membership: Membership) -> None:
    if membership.role == "owner":
        raise TeamError("owner_cannot_leave", 409)
    db.delete(membership)
    clear_active(db, membership.user_id, workspace.id)


def transfer(db, workspace: Workspace, owner: Membership, user_id: str) -> Membership:
    """Make ``user_id`` the owner; the previous owner stays as an admin. Exactly one owner at all times."""
    if owner.role != "owner":
        raise TeamError("owner_required", 403)
    target = _member(db, workspace.id, user_id)
    if target.user_id == owner.user_id:
        raise TeamError("already_owner", 409)
    target_user = db.get(User, target.user_id)
    if target_user is None or not target_user.is_active:
        raise TeamError("member_inactive", 409)
    owner.role, target.role = "admin", "owner"
    workspace.owner_id = target.user_id
    return target


def owner_count(db, workspace_id: str) -> int:
    return int(db.scalar(select(func.count()).select_from(Membership)
                         .where(Membership.workspace_id == workspace_id, Membership.role == "owner")) or 0)


# --- invitations ----------------------------------------------------------------------------------------------------

def invite(db, workspace: Workspace, actor: Membership, email: str, role: str) -> tuple[WorkspaceInvite, str]:
    email = email.strip().lower()
    if not EMAIL_PATTERN.fullmatch(email) or len(email) > 255:
        raise TeamError("invalid_email", 422)
    if role not in permissions.INVITABLE_ROLES:
        raise TeamError("invalid_role", 422)
    if not permissions.can_manage_member(actor.role, role):
        raise TeamError("cannot_manage_member", 403)
    existing = db.scalar(select(User).where(func.lower(User.email) == email))
    if existing is not None and db.get(Membership, (existing.id, workspace.id)) is not None:
        raise TeamError("already_member", 409)
    moment = _now()
    db.execute(update(WorkspaceInvite).where(WorkspaceInvite.workspace_id == workspace.id,
                                             func.lower(WorkspaceInvite.email) == email,
                                             WorkspaceInvite.accepted_at.is_(None),
                                             WorkspaceInvite.revoked_at.is_(None)).values(revoked_at=moment))
    raw = secrets.token_urlsafe(32)
    row = WorkspaceInvite(id=str(uuid.uuid4()), workspace_id=workspace.id, email=email, role=role,
                          token_hash=_digest(raw), invited_by_user_id=actor.user_id, created_at=moment,
                          expires_at=moment + INVITE_LIFETIME)
    db.add(row)
    return row, raw


def renew(db, invitation: WorkspaceInvite) -> str:
    """A fresh token and expiry for a pending invitation (resend)."""
    raw = secrets.token_urlsafe(32)
    invitation.token_hash, invitation.expires_at = _digest(raw), _now() + INVITE_LIFETIME
    return raw


def find(db, raw: str | None, *, lock: bool = False) -> WorkspaceInvite | None:
    if not raw or len(raw) > 200:
        return None
    query = select(WorkspaceInvite).where(WorkspaceInvite.token_hash == _digest(raw))
    if lock:
        query = query.with_for_update()
    return db.scalar(query)


def status(invitation: WorkspaceInvite | None) -> str:
    if invitation is None:
        return "invalid"
    if invitation.revoked_at is not None:
        return "revoked"
    if invitation.accepted_at is not None:
        return "accepted"
    if _aware(invitation.expires_at) <= _now():
        return "expired"
    return "pending"


def accept(db, raw: str, user: User) -> tuple[Workspace, WorkspaceInvite]:
    invitation = find(db, raw, lock=True)
    state = status(invitation)
    if state != "pending":
        raise TeamError(f"invite_{state}", 410 if state in ("expired", "revoked", "accepted") else 404)
    if invitation.email.lower() != user.email.lower():
        raise TeamError("invite_email_mismatch", 403)
    workspace = db.get(Workspace, invitation.workspace_id)
    if db.get(Membership, (user.id, workspace.id)) is None:
        db.add(Membership(user_id=user.id, workspace_id=workspace.id, role=invitation.role, created_at=_now()))
    invitation.accepted_at, invitation.accepted_by_user_id = _now(), user.id
    if user.email_verified_at is None:
        user.email_verified_at = _now()  # the invitation reached this inbox
    return workspace, invitation
