# Hike

<img src="docs/hike-icon.png" alt="hike-icon" width="100%"/>

## DDD

Implement entity, aggregate, value object(?via descriptors)
MAYBE: create descriptors for common value object validations

## Repository

there should be a generic repository and a repository for aggregates that auto-fill a lot of the functions

make specification type checked by using a generic specification that accepts model attributes as filter parameters.
in order to handle 
```python
MyAggregate.MyEntity.Price > 3
```
Each '.' should be a ForeignKey in SqlAlchemy, which is joined at runtime.
Each '.' should be an inner dictionary in PyMongo, no joining needed.

create Async repository
DBContext object
unit of work - only one that is generic via the DBContext!!!

MAYBE in the future: create IInsertionStrategy that would 

## Event driven

event handlers, bus, mapper
? create a repository that calls the event handlers

## hexagonal architecture

explain in docs
create interface for sending notifications(e.g. mail) and event(e.g. saga pattern)

## Other

implement CQRS: command(command, command handler, command mapper, maybe a command history) and query
create automatic services. for example: <https://knucklesuganda.github.io/py_assimilator/services/>
create a yaml configuration standard for things used here(i.e. database adapter)

## Resources and inspiration

These resources are used to give this library inspiration:

SQLAlchemy - https://docs.sqlalchemy.org/en/20/
C# Entity FrameWork Core - https://learn.microsoft.com/en-us/ef/
PyAssimilator - https://knucklesuganda.github.io/py_assimilator/

## TODO
just write the code I want it to look like
put claude code in plain mode
tell him to look at sqlalchemy
write tests so claude could check itself
tell him he can rewrite whatever he wants.