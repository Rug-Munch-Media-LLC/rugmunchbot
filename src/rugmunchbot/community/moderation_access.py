"""Trust list and admin authorisation for the moderation engine."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = ["AccessControl"]


class AccessControl:
    """Two independent allow-lists: trusted members and moderation admins."""

    def __init__(self, *, trusted: Iterable[int] = (), admins: Iterable[int] = ()) -> None:
        self._trusted = {int(u) for u in trusted}
        self._admins = {int(u) for u in admins}

    def is_trusted(self, user_id: int) -> bool:
        return int(user_id) in self._trusted

    def trust(self, user_id: int) -> None:
        self._trusted.add(int(user_id))

    def untrust(self, user_id: int) -> bool:
        if int(user_id) not in self._trusted:
            return False
        self._trusted.discard(int(user_id))
        return True

    def is_admin(self, user_id: int) -> bool:
        return int(user_id) in self._admins

    def summary(self) -> dict[str, Any]:
        return {"trusted": len(self._trusted), "admins": len(self._admins)}
