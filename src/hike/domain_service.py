from abc import ABC


class DomainService(ABC):
    """Orchestrates cross-aggregate validation and mutations.

    A domain service:

    - accepts one or more ``IRepository`` instances at construction time
    - wraps mutations in a ``UnitOfWork``
    - checks ``CrossAggregateRule`` instances before committing
    - never holds mutable domain state itself

    Example::

        class RegistrationService(DomainService):
            def __init__(self, user_repo: IRepository[UUID, User], uow: UnitOfWork) -> None:
                self._users = user_repo
                self._uow = uow

            def register(self, email: str) -> User:
                UniqueEmailRule().check(UniqueEmailContext(email, self._users))
                user = User(email=Email(email))
                with self._uow(self._users):
                    self._users.save(user)
                    self._uow.commit()
                return user
    """
