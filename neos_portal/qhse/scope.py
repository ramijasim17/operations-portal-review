"""QHSE uses the portal-wide department scope (see core/dept_scope.py)."""
from core.dept_scope import (  # noqa: F401
    ALL, BLOCKED, DEPT, NONE, Scope, asset_department_map, filter_qs, get_scope, initial_department, is_restricted,
    matches, user_department, visible_or_404,
)
