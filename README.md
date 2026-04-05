# Cliff

<img src="docs/cliff-icon.png" alt="cliff-icon" width="100%"/>

## DDD

Implement entity, aggregate, value object(?via descriptors)
MAYBE: create descriptors for common value object validations

## Repository

there should be a generic repository and a repository for aggregates that auto-fill a lot of the functions
make specification type checked by using a generic specification that accepts model attributes as filter parameters.
create Async repository
DBContext object
unit of work - only one that is generic via the DBContext!!!

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
