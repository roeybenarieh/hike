from hike import ValueObject, non_empty, non_negative


class Price(ValueObject[float]):
    __validators__ = [non_negative]


class Name(ValueObject[str]):
    __validators__ = [non_empty]


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

    # comparison
    print(p1.value)  # 5
    print(p1 == Price(5))  # True  — value-based equality
    print(p1 == p2)  # False
    print(p1 == p3)  # True
    print(p1 <= p3 < p2)  # True

    # arithmetic
    print(p2 + p1) # Price(value=11)
    print(p2 - p1) # Price(value=1)
    print(p2 * p1) # Price(value=30)
    print(p2 / p1) # Price(value=1.2)

    try:
        negative_price = p1 -p2
    except ValueError as err:
        print(err)
