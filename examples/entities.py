from cliff.ddd.entity import Field, UuidEntity
from cliff.ddd.specifications import EqualSpecification
from examples.value_objects import Name


class Ship(UuidEntity):
    name: Field[Name]


if __name__ == "__main__":
    s1 = Ship(name=Name("Titanic"))
    s2 = Ship(name=Name("Titanic"))  # different id → not equal
    s3 = Ship(id=s1.id, name=Name("Renamed"))  # same id → equal

    print(s1)  # Ship(id=UUID('...'), name=Name(value='Titanic'))
    print(s1 == s2)  # False — different ids
    print(s1 == s3)  # True  — same id, entity identity

    # Class-level access returns a FieldProxy for building specs
    spec: EqualSpecification = (Ship.name == "Titanic")
    print(type(spec).__name__)  # EqualSpecification