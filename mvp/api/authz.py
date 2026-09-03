"""Authorisation.

One rule, applied everywhere: a request handler never loads a row by id alone.
It loads it through a *scoped* statement built from the session's `Scope`, so an
out-of-scope id simply does not exist as far as the query is concerned.  That is
what makes IDOR (Insecure Direct Object Reference — guessing or pasting someone
else's record id into a URL) structurally impossible rather than merely unlikely.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import Select

from models import Role, User

# Roles that may read individual owner/animal records at all.
ENTITY_READ_ROLES = {Role.super_admin, Role.ulb_admin, Role.field_officer, Role.auditor}
# Roles that may create/modify registry data.
WRITE_ROLES = {Role.super_admin, Role.ulb_admin, Role.field_officer}
# Roles that see the whole district.
DISTRICT_WIDE_ROLES = {Role.super_admin, Role.auditor}
# Roles that may read the audit log.
AUDIT_ROLES = {Role.super_admin, Role.auditor}
# Roles that may manage users.
USER_ADMIN_ROLES = {Role.super_admin}


@dataclass(frozen=True)
class Scope:
    user_id: uuid.UUID
    username: str
    full_name: str
    role: Role
    ulb_id: int | None
    #: None means "every ULB"; otherwise the exact set this session may touch.
    ulb_ids: tuple[int, ...] | None

    # -- coarse capabilities ------------------------------------------------
    @property
    def district_wide(self) -> bool:
        return self.ulb_ids is None

    @property
    def stats_ulb_ids(self) -> tuple[int, ...] | None:
        """ULB restriction for *aggregate* queries.

        `ulb_ids` governs access to individual records, and for `viewer` it is
        deliberately empty — that role may not read a single owner or animal.
        Aggregates are a different question: the CM/press dashboard is meant to
        show the whole district, and carries no personal data to leak. So the
        viewer is district-wide here and nowhere else.
        """
        if self.role is Role.viewer:
            return None
        return self.ulb_ids

    @property
    def can_read_entities(self) -> bool:
        return self.role in ENTITY_READ_ROLES

    @property
    def can_write(self) -> bool:
        return self.role in WRITE_ROLES

    @property
    def can_manage_users(self) -> bool:
        return self.role in USER_ADMIN_ROLES

    @property
    def can_read_audit(self) -> bool:
        return self.role in AUDIT_ROLES

    @property
    def can_merge_owners(self) -> bool:
        return self.role is Role.super_admin

    # -- per-row PII decision ----------------------------------------------
    def sees_pii_for_ulb(self, ulb_id: int | None) -> bool:
        """Unmasked phone numbers: district-wide roles always; everyone else
        only inside their own ULB (SPEC §1.5)."""
        if self.role is Role.viewer:
            return False
        if self.district_wide:
            return True
        return ulb_id is not None and ulb_id in (self.ulb_ids or ())

    def may_write_ulb(self, ulb_id: int | None) -> bool:
        if not self.can_write or ulb_id is None:
            return False
        return self.district_wide or ulb_id in (self.ulb_ids or ())


def scope_for(user: User) -> Scope:
    if user.role in DISTRICT_WIDE_ROLES:
        ulb_ids: tuple[int, ...] | None = None
    elif user.role is Role.viewer:
        ulb_ids = ()  # no entity access at all
    else:
        ulb_ids = (user.ulb_id,) if user.ulb_id is not None else ()
    return Scope(
        user_id=user.id,
        username=user.username,
        full_name=user.full_name,
        role=user.role,
        ulb_id=user.ulb_id,
        ulb_ids=ulb_ids,
    )


def apply_ulb_scope(stmt: Select, column, scope: Scope) -> Select:
    """Attach the ULB restriction to a SELECT.

    # scoped query — prevents IDOR: callers must always route object lookups
    # through this rather than filtering on the primary key alone.
    """
    if scope.ulb_ids is None:
        return stmt
    if not scope.ulb_ids:
        # An empty scope must match nothing.  `IN ()` is not valid SQL, so use a
        # predicate that is always false.
        return stmt.where(column.in_([-1]))
    return stmt.where(column.in_(scope.ulb_ids))


def require_entity_read(scope: Scope) -> None:
    if not scope.can_read_entities:
        # 403: the role itself is wrong, which is not a secret.
        raise HTTPException(status_code=403, detail="role may not read records")


def require_write(scope: Scope) -> None:
    if not scope.can_write:
        raise HTTPException(status_code=403, detail="role may not write records")


def require_roles(scope: Scope, allowed: set[Role]) -> None:
    if scope.role not in allowed:
        raise HTTPException(status_code=403, detail="insufficient role")


def not_found() -> HTTPException:
    """Out-of-scope rows are reported as 404, never 403: a 403 would confirm the
    record exists (SPEC §7)."""
    return HTTPException(status_code=404, detail="not found")


def mask_name(name: str | None) -> str | None:
    """`Ram Kumar Yadav` -> `R. K. Y.` for callers outside the owning ULB."""
    if not name:
        return None
    parts = [p for p in name.split() if p]
    return " ".join(f"{p[0].upper()}." for p in parts) or None


def mask_phone(phone: str | None, visible: bool) -> str | None:
    """`9876543210` -> `98XXXXXX10` for anyone outside the owning ULB."""
    if not phone:
        return None
    if visible:
        return phone
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) < 6:
        return "XXXXXX"
    return f"{digits[:2]}{'X' * (len(digits) - 4)}{digits[-2:]}"
