"""Integration tests for UnitOfWork + PyMongoRepository against a real MongoDB
instance (single-node replica set) managed by testcontainers.

Run with::

    uv run pytest tests/hike/persistence/providers/pymongo/test_uow.py -v
"""
from __future__ import annotations

import multiprocessing
import time
from collections.abc import Callable
from typing import Any
from uuid import UUID

import pytest
from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.synchronous.client_session import ClientSession
from testcontainers.core.container import DockerContainer  # pyright: ignore[reportMissingTypeStubs]
from testcontainers.core.wait_strategies import LogMessageWaitStrategy  # pyright: ignore[reportMissingTypeStubs]

from hike.persistence.providers.pymongo import PyMongoDBContext, PyMongoRepository
from hike.persistence.uow import UnitOfWork

from tests.hike.conftest import Boat, Journey
from tests.hike.persistence.providers._subprocess_helpers import pymongo_insert_boat
from tests.hike.persistence.providers.parity_suite import CrossProcessWatchParitySuite, RepositoryParitySuite

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def mongo_client() -> MongoClient[dict[str, Any]]:  # type: ignore[misc]
    with (
        DockerContainer("mongo:7")
        .with_command("--replSet rs0 --bind_ip_all")
        .with_exposed_ports(27017)
        .waiting_for(LogMessageWaitStrategy("Waiting for connections")) as container  # pyright: ignore[reportUnknownMemberType]
    ):
        host = container.get_container_host_ip()
        port = int(container.get_exposed_port(27017))

        init_client: MongoClient[dict[str, Any]] = MongoClient(host=host, port=port, directConnection=True)
        init_client.admin.command(  # pyright: ignore[reportUnknownMemberType]
            "replSetInitiate",
            {"_id": "rs0", "members": [{"_id": 0, "host": "127.0.0.1:27017"}]},
        )
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

        client: MongoClient[dict[str, Any]] = MongoClient(
            host=host, port=port, directConnection=True, uuidRepresentation="standard"
        )
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


# ---------------------------------------------------------------------------
# Parity tests
# ---------------------------------------------------------------------------


class TestPyMongoRepositoryParity(RepositoryParitySuite, CrossProcessWatchParitySuite):
    @pytest.fixture
    def uow(self, mongo_context: PyMongoDBContext) -> UnitOfWork[ClientSession]:
        return UnitOfWork(mongo_context)

    @pytest.fixture
    def repo(self, boats_collection: Collection[dict[str, Any]]) -> PyMongoRepository[UUID, Boat]:
        return PyMongoRepository(boats_collection, Boat)

    @pytest.fixture
    def journey_repo(self, journeys_collection: Collection[dict[str, Any]]) -> PyMongoRepository[UUID, Journey]:
        return PyMongoRepository(journeys_collection, Journey)

    @pytest.fixture
    def cross_process_insert(self, mongo_client: MongoClient[dict[str, Any]]) -> Callable[[Boat], None]:
        host, port = next(iter(mongo_client.nodes))

        def _insert(boat: Boat) -> None:
            ctx = multiprocessing.get_context("spawn")
            p = ctx.Process(
                target=pymongo_insert_boat,
                args=(str(host), port, "test_db", "boats",
                      str(boat.name.value), boat.price.value),
            )
            p.start()
            p.join(timeout=10)
            assert p.exitcode == 0, f"cross-process insert failed with exit code {p.exitcode}"

        return _insert
