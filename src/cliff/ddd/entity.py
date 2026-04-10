from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar, dataclass_transform, get_type_hints, overload
from uuid import UUID, uuid4

from .common import DomainObject
from .specifications.specs import (
    EqualSpecification,
    NotEqualSpecification,
    GreaterThanSpecification,
    GreaterThanEqualSpecification,
    LessThanSpecification,
    LessThanEqualSpecification,
)
from .value_object import ValueObject

_T = TypeVar("_T", bound="ValueObject[Any]")


class FieldProxy:
    """Returned when a ``Field[T]``-annotated field is accessed at the *class* level.

    You never create one of these yourself — it is produced by
    ``_FieldDescriptor.__get__`` whenever the attribute is read from the class
    rather than from an instance.

    Its comparison operators produce Specification objects instead of booleans,
    enabling SQLAlchemy-style query syntax:

        Boat.price < 100        →  LessThanSpecification
        Boat.price == 50        →  EqualSpecification
        (Boat.price >= 10) & (Boat.price < 100)   →  AndSpecification

    The ``field_name``, ``field_type``, and ``entity_class`` attributes are used
    by Visitor implementations to look up the actual runtime value on a concrete
    entity instance.
    """

    def __init__(self, field_name: str, field_type: type, entity_class: type) -> None:
        self.field_name = field_name  # e.g. "price"
        self.field_type = field_type  # e.g. Price
        self.entity_class = entity_class  # e.g. Boat

    def __eq__(self, other: object) -> EqualSpecification:  # type: ignore[override]
        if isinstance(other, FieldProxy):
            return EqualSpecification(self, other is self)  # type: ignore[arg-type]
        return EqualSpecification(self, other)

    def __ne__(self, other: object) -> NotEqualSpecification:  # type: ignore[override]
        if isinstance(other, FieldProxy):
            return NotEqualSpecification(self, other is not self)  # type: ignore[arg-type]
        return NotEqualSpecification(self, other)

    def __lt__(self, other: object) -> LessThanSpecification:
        return LessThanSpecification(self, other)

    def __le__(self, other: object) -> LessThanEqualSpecification:
        return LessThanEqualSpecification(self, other)

    def __gt__(self, other: object) -> GreaterThanSpecification:
        return GreaterThanSpecification(self, other)

    def __ge__(self, other: object) -> GreaterThanEqualSpecification:
        return GreaterThanEqualSpecification(self, other)

    def __hash__(self) -> int:
        return hash((self.field_name, self.entity_class))

    def __repr__(self) -> str:
        return f"{self.entity_class.__name__}.{self.field_name}"


class Field(Generic[_T]):
    """Annotation wrapper for ValueObject fields on Entity subclasses.

    Use ``Field[T]`` instead of a bare ValueObject type annotation so that
    Pyright resolves class-level and instance-level access correctly::

        class Ship(UuidEntity):
            name: Field[Name]   # instead of: name: Name

    Pyright uses the descriptor protocol on this annotation type:

    - ``Ship.name``         →  ``FieldProxy``       (class-level, for spec building)
    - ``ship.name``         →  ``Name``              (instance-level, the ValueObject)
    - ``Ship.name == "x"``  →  ``EqualSpecification``  (no ``# type: ignore`` needed)

    The ``__set__`` signature tells ``@dataclass_transform`` that the generated
    ``__init__`` parameter type is ``T``, not ``Field[T]``.

    At runtime this class is never instantiated — ``Entity.__init_subclass__``
    replaces every ``Field[T]`` annotation with a ``_FieldDescriptor[T]``
    before any instance is created.
    """

    @overload
    def __get__(self, instance: None, owner: type) -> FieldProxy: ...

    @overload
    def __get__(self, instance: object, owner: type | None) -> _T: ...

    def __get__(self, instance: object, owner: type | None = None) -> FieldProxy | _T:
        # Never reached at runtime — see class docstring.
        raise NotImplementedError  # pragma: no cover

    def __set__(self, instance: object, value: _T) -> None:
        # Never reached at runtime — present only so @dataclass_transform infers
        # the __init__ parameter type as T rather than Field[T].
        raise NotImplementedError  # pragma: no cover


def _unwrap_field(ann_type: object) -> type[ValueObject[Any]] | None:
    """Return the inner ``T`` if *ann_type* is ``Field[T]``, else ``None``."""
    origin = getattr(ann_type, "__origin__", None)
    if origin is Field:
        args: tuple[Any, ...] = getattr(ann_type, "__args__", ())
        if args and isinstance(args[0], type) and issubclass(args[0], ValueObject):
            return args[0]  # type: ignore[return-value]
    return None


# Sentinel used to detect "field not yet set" without conflicting with None.
_MISSING = object()


class _FieldDescriptor(Generic[_T]):
    """Descriptor managing a single ValueObject-typed field on an Entity subclass.

    Installed automatically by ``Entity.__init_subclass__`` for every
    ``Field[T]``-annotated attribute, or explicitly via ``vo()``.

    **Three access modes:**

    1. *Class-level read* (``Ship.name``) — ``__get__(instance=None)`` returns
       a ``FieldProxy`` whose comparison operators build Specification objects.

    2. *Instance-level read* (``ship.name``) — ``__get__(instance=ship)``
       returns the stored ``ValueObject`` (type ``_T``).

    3. *Assignment* (``self.name = "Titanic"`` inside ``__init__``) — ``__set__``
       auto-converts to ``Name("Titanic")`` if the value isn't already the right type.

    Values are stored in ``instance.__dict__`` under the private key
    ``_vo_<name>`` so the descriptor stays in control on subsequent reads.
    """

    def __init__(self, field_name: str, field_type: type[_T]) -> None:
        self.field_name = field_name
        self.field_type = field_type
        self._private = f"_vo_{field_name}"

    def __set_name__(self, _owner: type, name: str) -> None:
        self.field_name = name
        self._private = f"_vo_{name}"

    @overload
    def __get__(self, instance: None, owner: type) -> FieldProxy:
        ...

    @overload
    def __get__(self, instance: object, owner: type | None) -> _T:
        ...

    def __get__(self, instance: object, owner: type | None = None) -> FieldProxy | _T:
        if instance is None:
            return FieldProxy(self.field_name, self.field_type, owner or type(None))
        val = instance.__dict__.get(self._private, _MISSING)
        if val is _MISSING:
            raise AttributeError(f"Field '{self.field_name}' not set")
        return val  # type: ignore[return-value]

    def __set__(self, instance: object, value: object) -> None:
        if not isinstance(value, self.field_type):
            value = self.field_type(value)
        instance.__dict__[self._private] = value


def vo(field_type: type[_T]) -> Field[_T]:
    """Declare a ValueObject field on an Entity explicitly.

    An alternative to the ``Field[T]`` annotation style when you want the
    descriptor to be clearly visible in the class body::

        class Boat(Entity):
            price: Field[Price] = vo(Price)   # explicit
            name: Field[Name]                 # implicit — same runtime behaviour

    Both forms install a ``_FieldDescriptor`` and are fully equivalent at
    runtime and to Pyright.
    """
    return _FieldDescriptor("", field_type)  # type: ignore[return-value]


@dataclass_transform(kw_only_default=True, field_specifiers=(vo,))
class Entity(DomainObject):
    """Base class for DDD entities.

    An entity has *identity*: two Entity objects are equal if and only if they
    share the same ``id``, regardless of the values of their other fields.

    ---

    ## Declaring an entity

    Subclass ``Entity`` and annotate ValueObject fields with ``Field[T]``:

        from cliff.ddd.entity import Entity, Field
        from cliff.ddd.value_object import ValueObject

        class Price(ValueObject[float]):
            value: float

        class Boat(Entity):
            id: UUID = field(default_factory=uuid4)
            name: str
            price: Field[Price]     # VO field — enables Boat.price < 100

    All fields are keyword-only.  VO fields auto-convert raw values on
    assignment: ``Boat(price=100)`` stores ``Price(100)`` internally.

    ---

    ## Class-level vs instance-level access

    ``Field[T]`` annotations install a descriptor (``_FieldDescriptor``) on the
    class via ``__init_subclass__``:

    - ``Boat.price``        →  ``FieldProxy``            (for spec building)
    - ``boat.price``        →  ``Price``                 (the actual ValueObject)
    - ``boat.price < 100``  →  ``bool``                  (compares ``.value``)
    - ``Boat.price < 100``  →  ``LessThanSpecification``

    Plain annotations (``id: UUID``, ``name: str``) work as normal dataclass
    fields — no spec-building magic, just data.

    ---

    ## Identity and equality

    ``__eq__`` compares by ``id`` only.  ``__hash__`` is also id-based so
    entities can be stored in sets and used as dict keys.
    """

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)

        # ── Step 1: add annotations for vo() fields ──────────────────────────
        # vo() fields placed in the class body without an explicit annotation
        # are invisible to dataclass() (which only processes annotated names).
        # We inject a Field[T] annotation so dataclass() picks them up.
        if not hasattr(cls, "__annotations__"):
            cls.__annotations__ = {}
        for name, attr in cls.__dict__.items():
            if isinstance(attr, _FieldDescriptor) and name not in cls.__annotations__:
                cls.__annotations__[name] = Field[attr.field_type]  # type: ignore[valid-type]

                # ── Step 2: turn the subclass into a dataclass ────────────────────────
        # eq=False  — don't overwrite our id-based __eq__/__hash__.
        # kw_only=True — all parameters are keyword-only, which lets a field
        #                with a default (e.g. id=field(default_factory=uuid4))
        #                appear before required fields without error.
        dataclass(cls, eq=False, kw_only=True)

        # ── Step 3: install _FieldDescriptor for Field[T]-annotated fields ───
        # get_type_hints() is used instead of __annotations__ directly because
        # `from __future__ import annotations` turns every annotation into a
        # string — get_type_hints() resolves those strings to the real types.
        try:
            own_names = set(cls.__annotations__)
            resolved = {k: v for k, v in get_type_hints(cls).items() if k in own_names}
        except Exception:
            resolved = {}

        for name, ann_type in resolved.items():
            inner = _unwrap_field(ann_type)
            if inner is not None and not isinstance(cls.__dict__.get(name), _FieldDescriptor):
                desc = _FieldDescriptor(name, inner)
                setattr(cls, name, desc)
                desc.__set_name__(cls, name)

    def __eq__(self, other: object) -> bool:
        if type(self) is not type(other):
            return False
        return self.id == other.id  # type: ignore[attr-defined]

    def __hash__(self) -> int:
        return hash(self.id)  # type: ignore[attr-defined]


class UuidValueObject(ValueObject[UUID]):
    ...


class UuidEntity(Entity):
    """Entity with a UUID primary key, auto-generated by default."""

    id: Field[UuidValueObject] = field(default_factory=uuid4)
