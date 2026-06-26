from __future__ import annotations

from collections.abc import Callable, Hashable
from dataclasses import dataclass, field, fields
from typing import Any, Generic, TypeVar, dataclass_transform, get_origin, get_type_hints, overload, cast
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

_T = TypeVar("_T")
_TVO = TypeVar("_TVO", bound="ValueObject[Any]")


class EntityID[TId: Hashable](ValueObject[TId]):
    """A ValueObject that wraps the raw identifier of a DDD entity.

    Use ``Field[EntityID[UUID]]`` (or any hashable raw type) in entity
    subclasses to get type-safe, VO-wrapped IDs that participate in
    spec-building via ``FieldProxy``.
    """


class TerminalFieldProxy:
    """Class-level proxy for ``Field[ValueObject]`` fields.

    Returned when a ``Field[T]``-annotated field is accessed at the class level
    and ``T`` is a ``ValueObject``.  Its comparison operators produce
    ``Specification`` objects for building queries:

        Boat.price < 100        →  LessThanSpecification
        Boat.price == 50        →  EqualSpecification
        (Boat.price >= 10) & (Boat.price < 100)   →  AndSpecification

    Does NOT support attribute chaining — use ``Field[Entity]`` (which returns
    a ``FieldProxy``) when you need to traverse to nested entity fields.
    """

    def __init__(
        self,
        field_name: str,
        field_type: type,
        entity_class: type,
        parent: FieldProxy | None = None,
    ) -> None:
        self.field_name = field_name
        self.field_type = field_type
        self.entity_class = entity_class
        self.parent = parent

    @property
    def path(self) -> list[str]:
        """Full field-name chain from root to leaf, e.g. ``['engine', 'price']``."""
        parts: list[str] = []
        node: TerminalFieldProxy | None = self
        while node is not None:
            parts.append(node.field_name)
            node = node.parent
        parts.reverse()
        return parts

    @property
    def root(self) -> TerminalFieldProxy:
        """The root proxy — the one directly on the queried class."""
        node: TerminalFieldProxy = self
        while node.parent is not None:
            node = node.parent
        return node

    def __eq__(self, other: object) -> EqualSpecification:  # pyright: ignore[reportIncompatibleMethodOverride]
        if isinstance(other, TerminalFieldProxy):
            return EqualSpecification(self, other is self)
        return EqualSpecification(self, other)

    def __ne__(self, other: object) -> NotEqualSpecification:  # pyright: ignore[reportIncompatibleMethodOverride]
        if isinstance(other, TerminalFieldProxy):
            return NotEqualSpecification(self, other is not self)
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
        return f"{self.root.entity_class.__name__}.{'.'.join(self.path)}"


class FieldProxy(TerminalFieldProxy):
    """Class-level proxy for ``Field[Entity]`` fields.

    Extends ``TerminalFieldProxy`` with attribute chaining, enabling nested specs:

        Boat.engine.price > 1_000   →  GreaterThanSpecification with path ["engine", "price"]

    The ``path`` property returns the full list of field names from root to leaf.
    Visitor implementations use it to traverse the object graph during evaluation.
    """

    def __getattr__(self, name: str) -> FieldProxy:
        """Enable chaining: ``Boat.engine.price`` returns a nested FieldProxy."""
        if name.startswith("_"):
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")
        for klass in self.field_type.__mro__:
            if name in klass.__dict__ and isinstance(klass.__dict__[name], _FieldDescriptor):
                desc: _FieldDescriptor[Any] = klass.__dict__[name]
                return FieldProxy(name, desc.field_type, self.field_type, parent=self)
        raise AttributeError(
            f"'{self.field_type.__name__}' has no Field attribute '{name}'. "
            f"Make sure it is annotated with Field[T]."
        )


class Field(Generic[_T]):
    """Annotation wrapper for any field on Entity subclasses — ValueObject or Entity-typed.

    Use ``Field[T]`` for both cases::

        class Engine(UuidEntity):
            price: Field[Price]            # ValueObject field

        class Boat(UuidEntity):
            engine: Field[Engine]          # Entity field — enables Boat.engine.price > 1_000
            price: Field[Price]            # ValueObject field — enables Boat.price == 100

    Pyright uses the descriptor protocol on this annotation type:

    - ``Boat.engine``        →  ``FieldProxy``      (class-level; supports chaining via __getattr__)
    - ``Boat.engine.price``  →  ``FieldProxy``      (chained — FieldProxy.__getattr__ handles it)
    - ``boat.engine``        →  ``Engine``           (instance-level, the entity)
    - ``boat.price``         →  ``Price``            (instance-level, the value object)
    - ``Boat.price == 100``  →  ``EqualSpecification``

    ``__get__`` uses self-type overloads to distinguish ValueObject fields
    (returning ``TerminalFieldProxy``) from Entity fields (returning ``FieldProxy``),
    so Pyright can enforce that only Entity-typed fields support chained access.

    ``__set__`` uses a single signature so that ``@dataclass_transform`` correctly
    infers the ``__init__`` parameter type as ``T`` rather than ``Field[T]``.

    At runtime this class is never instantiated — ``Entity.__init_subclass__``
    replaces every ``Field[T]`` annotation with a ``_FieldDescriptor[T]``
    before any instance is created.
    """

    @overload
    def __get__(self: Field[_TVO], instance: None, owner: type) -> TerminalFieldProxy: ...

    @overload
    def __get__(self, instance: None, owner: type) -> FieldProxy: ...

    @overload
    def __get__(self, instance: object, owner: type | None) -> _T: ...

    def __get__(self, instance: object, owner: type | None = None) -> TerminalFieldProxy | FieldProxy | _T:
        raise NotImplementedError  # pragma: no cover

    def __set__(self, instance: object, value: _T) -> None:
        raise NotImplementedError  # pragma: no cover


def unwrap_field(ann_type: object) -> type[ValueObject[Any]] | None:
    """Return the inner ``T`` if *ann_type* is ``Field[T]``, else ``None``.

    Handles both bare types (``Field[Price]``) and parameterized generics
    (``Field[EntityID[UUID]]``).  For parameterized generics the bare origin
    class is returned so that ``isinstance`` and constructor calls in
    ``_FieldDescriptor`` work correctly at runtime.
    """
    origin = getattr(ann_type, "__origin__", None)
    if origin is Field:
        args: tuple[Any, ...] = getattr(ann_type, "__args__", ())
        if not args:
            return None
        inner = args[0]
        # Bare class: Field[Price]
        if isinstance(inner, type) and issubclass(inner, ValueObject):
            return cast(type[ValueObject[Any]], inner)
        # Parameterized generic: Field[EntityID[UUID]]
        bare = get_origin(inner)
        if isinstance(bare, type) and issubclass(bare, ValueObject):
            return cast(type[ValueObject[Any]], bare)
    return None


def _unwrap_annotation(ann_type: object) -> type | None:
    """Return the inner ``T`` if *ann_type* is ``Field[T]``, else ``None``.

    Accepts any class as ``T`` (not just ValueObject subclasses), enabling
    ``Field[Engine]`` for Entity-typed fields.
    """
    origin = getattr(ann_type, "__origin__", None)
    if origin is not Field:
        return None
    args: tuple[Any, ...] = getattr(ann_type, "__args__", ())
    if not args:
        return None
    inner = args[0]
    if isinstance(inner, type):
        return inner
    bare = get_origin(inner)
    return bare if isinstance(bare, type) else None


# Sentinel used to detect "field not yet set" without conflicting with None.
_MISSING = object()


class _FieldDescriptor(Generic[_T]):
    """Descriptor managing a single field on an Entity subclass.

    Installed automatically by ``Entity.__init_subclass__`` for every
    ``Field[T]``-annotated attribute, or explicitly via ``vo()``.

    **Three access modes:**

    1. *Class-level read* (``Ship.name``) — ``__get__(instance=None)`` returns
       a ``FieldProxy`` whose comparison operators build Specification objects.

    2. *Instance-level read* (``ship.name``) — ``__get__(instance=ship)``
       returns the stored value (type ``_T``).

    3. *Assignment* (``self.name = "Titanic"`` inside ``__init__``) — ``__set__``
       auto-converts raw values to the field type **only** if the type is a
       ``ValueObject``.  For Entity-typed fields the value must already be the
       correct type.

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
        return cast(_T, val)

    def __set__(self, instance: object, value: object) -> None:
        ft: type[Any] = self.field_type  # narrow to type[Any] for runtime ops
        if isinstance(value, ft):
            instance.__dict__[self._private] = value
        elif issubclass(ft, ValueObject):
            instance.__dict__[self._private] = ft(value)
        else:
            raise TypeError(f"Expected {ft.__name__}, got {type(value).__name__}")


def vo(field_type: type[_TVO]) -> Field[_TVO]:
    """Declare a ValueObject field on an Entity explicitly.

    An alternative to the ``Field[T]`` annotation style when you want the
    descriptor to be clearly visible in the class body::

        class Boat(Entity):
            price: Field[Price] = vo(Price)   # explicit
            name: Field[Name]                 # implicit — same runtime behaviour

    Both forms install a ``_FieldDescriptor`` and are fully equivalent at
    runtime and to Pyright.
    """
    return _FieldDescriptor("", field_type)  # pyright: ignore[reportReturnType]


def vo_field(*, default_factory: Callable[[], _T]) -> Field[_T]:
    """Declare a ValueObject field with a default factory.

    Use instead of ``dataclasses.field(default_factory=…)`` when the field is
    annotated with ``Field[T]``.  Pyright cannot reconcile ``dataclasses.field``
    return types with our custom ``Field[T]`` descriptor type, so this helper
    bridges the gap.  The parameter is named ``default_factory`` so that
    ``@dataclass_transform`` (PEP 681) recognises the field as optional in the
    generated ``__init__``::

        class UuidEntity(Entity):
            id: Field[EntityID[UUID]] = vo_field(default_factory=lambda: EntityID(uuid4()))

    At runtime it simply delegates to ``dataclasses.field``.
    """
    return field(default_factory=default_factory)  # pyright: ignore[reportReturnType]


@dataclass_transform(kw_only_default=True, field_specifiers=(vo, vo_field))
class Entity[TId: Hashable](DomainObject):
    """Base class for DDD entities, generic over the raw ID type ``TId``.

    An entity has *identity*: two Entity objects are equal if and only if they
    share the same ``id``, regardless of the values of their other fields.

    ``TId`` is the raw hashable type wrapped inside the ``EntityID`` value
    object (e.g. ``UUID``, ``int``, ``str``).  Concrete subclasses pin the
    type parameter:

        class UuidEntity(Entity[UUID]):
            id: Field[EntityID[UUID]] = vo_field(default_factory=lambda: EntityID(uuid4()))

    ---

    ## Declaring an entity

    Subclass a concrete base (e.g. ``UuidEntity``) and annotate ValueObject
    fields with ``Field[T]``:

        from hike.ddd.entity import Field, UuidEntity
        from hike.ddd.value_object import ValueObject

        class Price(ValueObject[float]):
            value: float

        class Boat(UuidEntity):
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

    Plain annotations (``name: str``) work as normal dataclass fields — no
    spec-building magic, just data.

    ---

    ## Identity and equality

    ``__eq__`` compares by ``id`` only.  ``__hash__`` is also id-based so
    entities can be stored in sets and used as dict keys.
    """

    id: Field[EntityID[TId]]

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
                cls.__annotations__[name] = Field[Any]

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
            inner = _unwrap_annotation(ann_type)
            if inner is not None and not isinstance(cls.__dict__.get(name), _FieldDescriptor):
                desc: _FieldDescriptor[Any] = _FieldDescriptor(name, inner)  # pyright: ignore[reportArgumentType,reportUnknownVariableType]
                setattr(cls, name, desc)
                desc.__set_name__(cls, name)

    def __eq__(self, other: object) -> bool:
        if type(self) is not type(other):
            return False
        return self.id == cast(Entity[Any], other).id

    def __hash__(self) -> int:
        return hash(self.id)


##### Entity utility functions ####

def to_dict(entity: Entity[Any]) -> dict[str, Any]:
    """Serialize *entity* to a plain dict suitable for MongoDB storage.

    ValueObject fields are flattened to their raw ``.value`` so the document
    stores e.g. ``{"price": 4.99}`` rather than ``{"price": {"value": 4.99}}``.
    List fields are serialized element-by-element with the same rule.
    """
    result: dict[str, Any] = {}
    for f in get_fields(entity):
        if not f.init:
            continue
        val: Any = getattr(cast(Any, entity), f.name)
        if isinstance(val, ValueObject):
            result[f.name] = cast(Any, val).value
        elif isinstance(val, list):
            result[f.name] = [
                cast(Any, v).value if isinstance(v, ValueObject) else v
                for v in cast(list[Any], val)
            ]
        else:
            result[f.name] = val
    return result


def get_fields(entity: Entity[Any] | type[Entity[Any]]):
    return fields(cast(Any, entity))


##### Base Entity implementations ####

class UuidEntity(Entity[UUID]):
    """Entity with a UUID primary key, auto-generated by default."""

    id: Field[EntityID[UUID]] = vo_field(default_factory=lambda: EntityID(uuid4()))
