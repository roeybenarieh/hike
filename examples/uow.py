"""Integration tests for UnitOfWork + PyMongoRepository.

Uses testcontainers to spin up a MongoDB replica-set, which is required for
ACID multi-document transactions.  Run with::

    uv run pytest examples/uow.py -v
"""
from __future__ import annotations

import time
from typing import Any, Generator, cast
from uuid import UUID

import pytest
from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.errors import PyMongoError
from testcontainers.core.container import DockerContainer  # type: ignore[import-untyped]
from testcontainers.core.wait_strategies import LogMessageWaitStrategy  # type: ignore[import-untyped]

from cliff.ddd.aggregate import UuidAggregate
from cliff.ddd.db_contexts import PyMongoDBContext
from cliff.ddd.entity import Field
from cliff.ddd.repository import AggregateDoesNotExistError, PyMongoRepository
from cliff.ddd.uow import UnitOfWork
from cliff.ddd.value_object import ValueObject


class Price(ValueObject[float]):
    def __post_init__(self) -> None:
        if self.value < 0:
            raise ValueError("Price cannot be negative")


# ---------------------------------------------------------------------------
# Domain model
# ---------------------------------------------------------------------------


class Boat(UuidAggregate):
    name: str
    price: Field[Price]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def mongo_client() -> Generator[MongoClient[dict[str, Any]], None, None]:
    """Start a MongoDB replica-set container (required for ACID transactions).

    Standalone MongoDB does not support multi-document transactions; a
    replica-set is the minimum topology that enables them.

    DockerContainer is used directly (rather than MongoDbContainer) so that
    no MONGO_INITDB_ROOT_* env vars are set — those trigger an initialization
    script that is incompatible with --replSet mode.
    """
    container = (
        DockerContainer("mongo:7")
        .with_command("mongod --replSet rs0 --bind_ip_all")
        .with_exposed_ports(27017)
        .waiting_for(LogMessageWaitStrategy("Waiting for connections"))
    )
    with container:

        # Use the container's internal bridge IP (e.g. 172.17.0.x).
        # On Linux, the host can reach this address directly, and it is
        # consistent with what MongoDB uses for intra-replica-set traffic.
        # This avoids the host-port ↔ container-port mismatch that breaks
        # replica-set topology discovery.
        container._container.reload()  # type: ignore[union-attr]
        container_ip: str = container._container.attrs[  # type: ignore[union-attr]
            "NetworkSettings"
        ]["Networks"]["bridge"]["IPAddress"]
        mongo_uri = f"mongodb://{container_ip}:27017"

        # Short-lived client to initiate the replica set.
        init_client: MongoClient[dict[str, Any]] = MongoClient(
            mongo_uri,
            directConnection=True,
            serverSelectionTimeoutMS=10_000,
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                init_client.admin.command(
                    "replSetInitiate",
                    {"_id": "rs0", "members": [{"_id": 0, "host": f"{container_ip}:27017"}]},
                )
                break
            except PyMongoError:
                time.sleep(0.5)
        else:
            raise RuntimeError("Failed to initiate replica set within 30 s")
        init_client.close()

        # Reconnect without directConnection so pymongo uses replica-set
        # topology, which enables session / transaction support.
        # uuidRepresentation="standard" lets pymongo encode Python UUID objects
        # as BSON binary subtype 4 automatically.
        client: MongoClient[dict[str, Any]] = MongoClient(
            f"{mongo_uri}/?replicaSet=rs0",
            serverSelectionTimeoutMS=30_000,
            uuidRepresentation="standard",
        )

        # Wait for primary election
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                status = client.admin.command("replSetGetStatus")
                if any(
                    m.get("stateStr") == "PRIMARY"
                    for m in status.get("members", [])
                ):
                    break
            except PyMongoError:
                pass
            time.sleep(0.5)
        else:
            raise RuntimeError(
                "MongoDB replica-set primary did not elect within 30 s"
            )

        yield client
        client.close()


@pytest.fixture
def boats(
    mongo_client: MongoClient[dict[str, Any]],
) -> Generator[Collection[dict[str, Any]], None, None]:
    """Yield the 'boats' collection; drops it after each test for isolation."""
    coll: Collection[dict[str, Any]] = mongo_client["cliff_test"]["boats"]
    yield coll
    coll.drop()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_uow(
    client: MongoClient[dict[str, Any]],
    collection: Collection[dict[str, Any]],
) -> UnitOfWork[Any, Any]:
    context = PyMongoDBContext(client=client)
    repo: PyMongoRepository[UUID] = PyMongoRepository(
        collection=collection,
        aggregate_class=Boat,
    )
    return UnitOfWork(context, repo=repo)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_save_and_get_one(
    mongo_client: MongoClient[dict[str, Any]],
    boats: Collection[dict[str, Any]],
) -> None:
    boat = Boat(name="Sea Spirit", price=Price(4_999.99))

    with make_uow(mongo_client, boats) as uow:
        uow.repo.save(boat)
        uow.commit()

    with make_uow(mongo_client, boats) as uow:
        fetched = cast(Boat, uow.repo.get_one(boat.id.value))

    assert fetched.name == "Sea Spirit"
    assert fetched.price == Price(4_999.99)


def test_update(
    mongo_client: MongoClient[dict[str, Any]],
    boats: Collection[dict[str, Any]],
) -> None:
    boat = Boat(name="Old Name", price=Price(100.0))

    with make_uow(mongo_client, boats) as uow:
        uow.repo.save(boat)
        uow.commit()

    boat.price = Price(200.0)  # type: ignore[assignment]
    with make_uow(mongo_client, boats) as uow:
        uow.repo.update(boat)
        uow.commit()

    with make_uow(mongo_client, boats) as uow:
        fetched = cast(Boat, uow.repo.get_one(boat.id.value))

    assert fetched.price == Price(200.0)


def test_get_many_with_spec(
    mongo_client: MongoClient[dict[str, Any]],
    boats: Collection[dict[str, Any]],
) -> None:
    boat_a = Boat(name="Alpha", price=Price(10.0))
    boat_b = Boat(name="Beta", price=Price(50.0))

    with make_uow(mongo_client, boats) as uow:
        uow.repo.save(boat_a)
        uow.repo.save(boat_b)
        uow.commit()

    with make_uow(mongo_client, boats) as uow:
        results = uow.repo.get_many(Boat.price > 20.0)

    assert len(results) == 1
    assert cast(Boat, results[0]).name == "Beta"


def test_upsert_creates_then_updates(
    mongo_client: MongoClient[dict[str, Any]],
    boats: Collection[dict[str, Any]],
) -> None:
    boat = Boat(name="Ghost", price=Price(1.0))

    with make_uow(mongo_client, boats) as uow:
        uow.repo.upsert(boat)  # create
        uow.commit()

    boat.price = Price(2.0)  # type: ignore[assignment]
    with make_uow(mongo_client, boats) as uow:
        uow.repo.upsert(boat)  # update
        uow.commit()

    with make_uow(mongo_client, boats) as uow:
        fetched = cast(Boat, uow.repo.get_one(boat.id.value))

    assert fetched.price == Price(2.0)


def test_delete(
    mongo_client: MongoClient[dict[str, Any]],
    boats: Collection[dict[str, Any]],
) -> None:
    boat = Boat(name="Doomed", price=Price(0.01))

    with make_uow(mongo_client, boats) as uow:
        uow.repo.save(boat)
        uow.commit()

    with make_uow(mongo_client, boats) as uow:
        uow.repo.delete(boat)
        uow.commit()

    with make_uow(mongo_client, boats) as uow:
        with pytest.raises(AggregateDoesNotExistError):
            uow.repo.get_one(boat.id.value)


def test_rollback_on_exception(
    mongo_client: MongoClient[dict[str, Any]],
    boats: Collection[dict[str, Any]],
) -> None:
    """Exception inside the UoW block must abort the transaction (ACID)."""
    boat = Boat(name="Rollback Boat", price=Price(99.0))

    with pytest.raises(ValueError, match="simulated failure"):
        with make_uow(mongo_client, boats) as uow:
            uow.repo.save(boat)
            raise ValueError("simulated failure")
    # transaction was aborted; the boat must not exist

    with make_uow(mongo_client, boats) as uow:
        with pytest.raises(AggregateDoesNotExistError):
            uow.repo.get_one(boat.id.value)
