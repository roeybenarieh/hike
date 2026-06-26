from hike.ddd.value_object import ValueObject


class Price(ValueObject[float]):
    def __post_init__(self) -> None:
        if self.value < 0:
            raise ValueError("Price cannot be negative")


class Name(ValueObject[str]):
    def __post_init__(self) -> None:
        if not self.value:
            raise ValueError("Name cannot be empty")


if __name__ == "__main__":
    try:
        Price(-1)
    except ValueError as err:
        print(err)

    try:
        result = Price(5) < Name("something")  # type: ignore[operator]
    except TypeError as err:
        print(err)

    p1 = Price(5)
    p2 = Price(6)
    p3 = Price(5)

    print(p1.value)  # 5
    print(p1 == Price(5))  # True  — value-based equality
    print(p1 == p2)  # False
    print(p1 == p3)  # True
    print(p1 <= p3 < p2)  # True
