from datetime import datetime

from hike import EqualSpecification, Field, UuidEntity, vo_field, ValueObject
from examples.value_objects import Name


class DateTime(ValueObject[datetime]):
    ...


class Ship(UuidEntity):
    name: Field[Name]
    created_at: Field[DateTime] = vo_field(default_factory=lambda: DateTime(datetime.now()))
    # something: str # TODO: should be banned


if __name__ == "__main__":
    s1 = Ship(name=Name("Titanic"))
    s2 = Ship(name=Name("Titanic"))  # different id → not equal
    s3 = Ship(id=s1.id, name=Name("Renamed"))  # same id → equal

    print(s1)  # Ship(id=UUID('...'), name=Name(value='Titanic'), created_at=datetime(...))
    print(s1 == s2)  # False — different ids
    print(s1 == s3)  # True  — same id, entity identity

    # Class-level access returns a FieldProxy for building specs
    spec: EqualSpecification = (Ship.name == "Titanic")
    print(type(spec).__name__)  # EqualSpecification
