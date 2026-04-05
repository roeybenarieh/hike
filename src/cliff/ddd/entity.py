from dataclasses import dataclass
from typing import Hashable
from uuid import UUID

from .common import DomainObject
from .value_object import ValueObject


@dataclass(frozen=True)
class EntityID[TId: Hashable](ValueObject):
    value: TId


class Entity[TId: Hashable](DomainObject):
    id: EntityID[TId]

    def __eq__(self, other: object) -> bool:
        if isinstance(other, type(self)):
            return self.id == other.id
        return False

    def __hash__(self) -> int:
        return hash(self.id)


class UUIDEntity(Entity[UUID]): ...
