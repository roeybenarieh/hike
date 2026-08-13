from hike import Field, UuidEntity, ValueObject


class Price(ValueObject[float]):
    def __post_init__(self) -> None:
        if self.value < 0:
            raise ValueError("Price cannot be negative")


class BoatName(ValueObject[str]):
    def __post_init__(self) -> None:
        if not self.value:
            raise ValueError("Boat name cannot be empty")


class Boat(UuidEntity):
    name: Field[BoatName]
    price: Field[Price]

    def discount_price(self) -> Price:
        return Price(self.price.value * 0.9)


def main() -> None:
    boat = Boat(name=BoatName("My Boat"), price=Price(100))

    print(f"Boat: {boat}")
    print(f"Price value: {boat.price.value}")
    print(f"Discounted: {boat.discount_price().value}")

    cheap_boat = Boat.price < 100  # runtime: LessThanSpecification
    print(f"Spec: {cheap_boat}")
    print(f"Spec type: {type(cheap_boat).__name__}")

    is_my_boat_cheap: bool = boat.price < 100
    print(f"Is my boat cheap: {is_my_boat_cheap}")


if __name__ == "__main__":
    main()