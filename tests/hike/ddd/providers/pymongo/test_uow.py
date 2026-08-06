"""Integration tests for UnitOfWork + PyMongoRepository against a real MongoDB
instance (single-node replica set) managed by testcontainers.

Run with::

    uv run pytest tests/hike/ddd/providers/pymongo/test_uow.py -v
"""
from __future__ import annotations

import time
from typing import Any
from uuid import UUID

import pytest
from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.synchronous.client_session import ClientSession
from testcontainers.core.container import DockerContainer  # pyright: ignore[reportMissingTypeStubs]
from testcontainers.core.wait_strategies import LogMessageWaitStrategy  # pyright: ignore[reportMissingTypeStubs]

from hike.ddd.entity import EntityID
from hike.ddd.providers.pymongo import PyMongoDBContext, PyMongoRepository
from hike.ddd.repository import AggregateAlreadyExistError, AggregateDoesNotExistError, OptimisticLockError
from hike.ddd.uow import UnitOfWork

from tests.hike.ddd.conftest import Boat, Checkpoint, Journey, Name, Price


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def mongo_client() -> MongoClient[dict[str, Any]]:  # type: ignore[misc]
    # Use DockerContainer directly to avoid MongoDbContainer's auth env vars, which
    # cause the Docker entrypoint to bind only to 127.0.0.1 during initialization.
    with (
        DockerContainer("mongo:7")
        .with_command("--replSet rs0 --bind_ip_all")
        .with_exposed_ports(27017)
        .waiting_for(LogMessageWaitStrategy("Waiting for connections")) as container  # pyright: ignore[reportUnknownMemberType]
    ):
        host = container.get_container_host_ip()
        port = int(container.get_exposed_port(27017))

        # Initiate the replica set using a temporary client, then discard it.
        # Connections established before replSetInitiate cache logicalSessionTimeoutMinutes
        # as None (standalone), so the final client must be created after initiation.
        init_client: MongoClient[dict[str, Any]] = MongoClient(host=host, port=port, directConnection=True)
        init_client.admin.command(  # pyright: ignore[reportUnknownMemberType]
            "replSetInitiate",
            {"_id": "rs0", "members": [{"_id": 0, "host": "127.0.0.1:27017"}]},
        )
        # Wait until the node is writable primary.
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                hello = init_client.admin.command("hello")  # pyright: ignore[reportUnknownMemberType]
                if hello.get("isWritablePrimary"):
                    break
            except Exception:
                pass
            time.sleep(0.5)
        init_client.close()

        # Fresh client — its connections see the replica-set HELLO with session support.
        client: MongoClient[dict[str, Any]] = MongoClient(host=host, port=port, directConnection=True)
        yield client  # type: ignore[misc]
        client.close()


@pytest.fixture(scope="session")
def mongo_context(mongo_client: MongoClient[dict[str, Any]]) -> PyMongoDBContext:  # type: ignore[misc]
    ctx = PyMongoDBContext(mongo_client)
    yield ctx  # type: ignore[misc]
    ctx.client.close()


@pytest.fixture(scope="session")
def boats_collection(mongo_context: PyMongoDBContext) -> Collection[dict[str, Any]]:
    db = mongo_context.client["test_db"]  # pyright: ignore[reportUnknownVariableType]
    col: Collection[dict[str, Any]] = db["boats"]  # pyright: ignore[reportUnknownVariableType]
    col.create_index("id", unique=True)
    return col


@pytest.fixture(scope="session")
def journeys_collection(mongo_context: PyMongoDBContext) -> Collection[dict[str, Any]]:
    db = mongo_context.client["test_db"]  # pyright: ignore[reportUnknownVariableType]
    col: Collection[dict[str, Any]] = db["journeys"]  # pyright: ignore[reportUnknownVariableType]
    col.create_index("id", unique=True)
    return col


@pytest.fixture(autouse=True)
def clear_collection(
    boats_collection: Collection[dict[str, Any]],
    journeys_collection: Collection[dict[str, Any]],
) -> None:  # type: ignore[misc]
    yield  # type: ignore[misc]
    boats_collection.delete_many({})
    journeys_collection.delete_many({})


@pytest.fixture
def uow(mongo_context: PyMongoDBContext, boats_collection: Collection[dict[str, Any]]) -> UnitOfWork[ClientSession, UUID, Boat]:
    repo: PyMongoRepository[UUID, Boat] = PyMongoRepository(boats_collection, Boat)
    return UnitOfWork(mongo_context, repo=repo)


@pytest.fixture
def journey_uow(mongo_context: PyMongoDBContext, journeys_collection: Collection[dict[str, Any]]) -> UnitOfWork[ClientSession, UUID, Journey]:
    repo: PyMongoRepository[UUID, Journey] = PyMongoRepository(journeys_collection, Journey)
    return UnitOfWork(mongo_context, repo=repo)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_save_and_get_one(uow: UnitOfWork[ClientSession, UUID, Boat]) -> None:
    boat = Boat(name=Name("Sea Spirit"), price=Price(4_999.99))

    with uow:
        uow.repo.save(boat)
        uow.commit()

    with uow:
        fetched = uow.repo.get_one(boat.id)

    assert fetched.name == Name("Sea Spirit")
    assert fetched.price == Price(4_999.99)


def test_update(uow: UnitOfWork[ClientSession, UUID, Boat]) -> None:
    boat = Boat(name=Name("Old Name"), price=Price(100.0))

    with uow:
        uow.repo.save(boat)
        uow.commit()

    boat.price = Price(200.0)
    with uow:
        uow.repo.update(boat)
        uow.commit()

    with uow:
        fetched = uow.repo.get_one(boat.id)

    assert fetched.price == Price(200.0)


def test_get_many_with_spec(uow: UnitOfWork[ClientSession, UUID, Boat]) -> None:
    boat_a = Boat(name=Name("Alpha"), price=Price(10.0))
    boat_b = Boat(name=Name("Beta"), price=Price(50.0))

    with uow:
        uow.repo.save(boat_a)
        uow.repo.save(boat_b)
        uow.commit()

    with uow:
        results = uow.repo.get_many(Boat.price > 20.0)

    assert len(results) == 1
    assert results[0].name == Name("Beta")


def test_upsert_creates_then_updates(uow: UnitOfWork[ClientSession, UUID, Boat]) -> None:
    boat = Boat(name=Name("Ghost"), price=Price(1.0))

    with uow:
        uow.repo.upsert(boat)
        uow.commit()

    boat.price = Price(2.0)
    with uow:
        uow.repo.upsert(boat)
        uow.commit()

    with uow:
        fetched = uow.repo.get_one(boat.id)

    assert fetched.price == Price(2.0)


def test_delete(uow: UnitOfWork[ClientSession, UUID, Boat]) -> None:
    boat = Boat(name=Name("Doomed"), price=Price(0.01))

    with uow:
        uow.repo.save(boat)
        uow.commit()

    with uow:
        uow.repo.delete(boat)
        uow.commit()

    with uow:
        with pytest.raises(AggregateDoesNotExistError):
            uow.repo.get_one(boat.id)


def test_save_duplicate_raises(uow: UnitOfWork[ClientSession, UUID, Boat]) -> None:
    boat = Boat(name=Name("Twin"), price=Price(50.0))

    with uow:
        uow.repo.save(boat)
        uow.commit()

    with pytest.raises(AggregateAlreadyExistError):
        with uow:
            uow.repo.save(boat)
            uow.commit()


def test_get_one_missing_raises(uow: UnitOfWork[ClientSession, UUID, Boat]) -> None:
    from uuid import uuid4

    with uow:
        with pytest.raises(AggregateDoesNotExistError):
            uow.repo.get_one(EntityID(uuid4()))


def test_delete_missing_raises(uow: UnitOfWork[ClientSession, UUID, Boat]) -> None:
    ghost = Boat(name=Name("Never Saved"), price=Price(1.0))

    with uow:
        with pytest.raises(AggregateDoesNotExistError):
            uow.repo.delete(ghost)


def test_rollback_on_exception(uow: UnitOfWork[ClientSession, UUID, Boat]) -> None:
    boat = Boat(name=Name("Rollback Boat"), price=Price(99.0))

    with pytest.raises(ValueError, match="simulated failure"):
        with uow:
            uow.repo.save(boat)
            raise ValueError("simulated failure")

    with uow:
        with pytest.raises(AggregateDoesNotExistError):
            uow.repo.get_one(boat.id)


def test_optimistic_lock_conflict(uow: UnitOfWork[ClientSession, UUID, Boat]) -> None:
    """Second writer loses when it holds a stale version."""
    boat = Boat(name=Name("Contested"), price=Price(100.0))

    with uow:
        uow.repo.save(boat)
        uow.commit()

    with uow:
        copy_a = uow.repo.get_one(boat.id)
    with uow:
        copy_b = uow.repo.get_one(boat.id)

    assert copy_a.version == 0
    assert copy_b.version == 0

    copy_a.price = Price(200.0)
    with uow:
        uow.repo.update(copy_a)
        uow.commit()
    assert copy_a.version == 1

    copy_b.price = Price(300.0)
    with pytest.raises(OptimisticLockError):
        with uow:
            uow.repo.update(copy_b)
            uow.commit()


def test_journey_save_and_get_one_with_checkpoints(journey_uow: UnitOfWork[ClientSession, UUID, Journey]) -> None:
    cp1 = Checkpoint(name=Name("Paris"))
    cp2 = Checkpoint(name=Name("Lyon"))
    journey = Journey(name=Name("France Trip"), checkpoints=[cp1, cp2])

    with journey_uow:
        journey_uow.repo.save(journey)
        journey_uow.commit()

    with journey_uow:
        fetched = journey_uow.repo.get_one(journey.id)

    assert fetched.name == Name("France Trip")
    assert len(fetched.checkpoints) == 2
    assert {cp.name for cp in fetched.checkpoints} == {Name("Paris"), Name("Lyon")}


def test_journey_update_checkpoints(journey_uow: UnitOfWork[ClientSession, UUID, Journey]) -> None:
    journey = Journey(name=Name("Tour"), checkpoints=[Checkpoint(name=Name("A"))])

    with journey_uow:
        journey_uow.repo.save(journey)
        journey_uow.commit()

    journey.checkpoints.append(Checkpoint(name=Name("B")))
    with journey_uow:
        journey_uow.repo.update(journey)
        journey_uow.commit()

    with journey_uow:
        fetched = journey_uow.repo.get_one(journey.id)

    assert len(fetched.checkpoints) == 2
