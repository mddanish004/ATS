"""Centralized RBAC permission definitions.

Single source of truth for the PRD permission model. Routers must express
authorization as ``require_permission(Permission.X)`` rather than scattering
hard-coded role checks. The backend is the authoritative security boundary;
frontend permission hints must never be trusted.
"""

from enum import Enum

from app.models.membership import MembershipRole


class Permission(str, Enum):
    # Jobs
    JOBS_READ = "jobs.read"
    JOBS_CREATE = "jobs.create"
    JOBS_UPDATE = "jobs.update"
    JOBS_PUBLISH = "jobs.publish"
    JOBS_APPROVE = "jobs.approve"
    # Candidates
    CANDIDATES_READ = "candidates.read"
    CANDIDATES_CREATE = "candidates.create"
    CANDIDATES_UPDATE = "candidates.update"
    CANDIDATES_DELETE = "candidates.delete"
    CANDIDATES_EXPORT = "candidates.export"
    # Applications
    APPLICATIONS_READ = "applications.read"
    APPLICATIONS_UPDATE = "applications.update"
    # Interviews
    INTERVIEWS_READ = "interviews.read"
    INTERVIEWS_CREATE = "interviews.create"
    INTERVIEWS_FEEDBACK = "interviews.feedback"
    # Offers
    OFFERS_READ = "offers.read"
    OFFERS_CREATE = "offers.create"
    OFFERS_APPROVE = "offers.approve"
    # Other
    ANALYTICS_READ = "analytics.read"
    USERS_MANAGE = "users.manage"
    AUDIT_READ = "audit.read"
    SETTINGS_MANAGE = "settings.manage"


_ALL_PERMISSIONS = frozenset(Permission)

# Role -> granted permissions. Least-privilege mapping derived from the PRD
# role responsibilities:
# - ADMIN: full control, including team, audit, and settings management.
# - RECRUITER: owns the pipeline day-to-day (jobs, candidates incl. deletion
#   and explicit export grant, applications, scheduling interviews, offers),
#   but cannot approve jobs/offers or submit interview feedback.
# - HIRING_MANAGER: approves jobs/offers, reviews candidates and feedback,
#   moves applications; no job authoring, candidate mutation, or admin areas.
# - INTERVIEWER: narrow scope -- assigned-interview context only (job and
#   candidate reads plus feedback submission; object-level assignment scoping
#   for interviews/candidates is Day 4+ domain work).
ROLE_PERMISSIONS: dict[MembershipRole, frozenset[Permission]] = {
    MembershipRole.ADMIN: _ALL_PERMISSIONS,
    MembershipRole.RECRUITER: frozenset(
        {
            Permission.JOBS_READ,
            Permission.JOBS_CREATE,
            Permission.JOBS_UPDATE,
            Permission.JOBS_PUBLISH,
            Permission.CANDIDATES_READ,
            Permission.CANDIDATES_CREATE,
            Permission.CANDIDATES_UPDATE,
            Permission.CANDIDATES_DELETE,
            Permission.CANDIDATES_EXPORT,
            Permission.APPLICATIONS_READ,
            Permission.APPLICATIONS_UPDATE,
            Permission.INTERVIEWS_READ,
            Permission.INTERVIEWS_CREATE,
            Permission.OFFERS_READ,
            Permission.OFFERS_CREATE,
            Permission.ANALYTICS_READ,
        }
    ),
    MembershipRole.HIRING_MANAGER: frozenset(
        {
            Permission.JOBS_READ,
            Permission.JOBS_APPROVE,
            Permission.CANDIDATES_READ,
            Permission.APPLICATIONS_READ,
            Permission.APPLICATIONS_UPDATE,
            Permission.INTERVIEWS_READ,
            Permission.INTERVIEWS_FEEDBACK,
            Permission.OFFERS_READ,
            Permission.OFFERS_APPROVE,
            Permission.ANALYTICS_READ,
        }
    ),
    MembershipRole.INTERVIEWER: frozenset(
        {
            Permission.JOBS_READ,
            Permission.CANDIDATES_READ,
            Permission.INTERVIEWS_READ,
            Permission.INTERVIEWS_FEEDBACK,
        }
    ),
}


def has_permission(role: MembershipRole, permission: Permission) -> bool:
    """Return True when ``role`` grants ``permission``."""
    return permission in ROLE_PERMISSIONS[role]
