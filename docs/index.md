# Welcome to Hike

**Hike** is a modern, type‑safe Python library of Domain‑Driven Design (DDD) building blocks.  

If you are new to **Domain‑Driven Design**, **Repository** patterns, **Unit of Work**, **Specifications**, or **Domain Events**, that’s perfectly fine. This documentation explains every concept from the ground up, using simple analogies, beginner‑friendly language, and practical code examples.

---

## What is Domain‑Driven Design (DDD)?

When building software, many developers start by designing **database tables** (rows, columns, foreign keys). DDD flips this approach on its head: **you start by modeling the real world** (the business domain) with Python classes and objects.

Instead of thinking about SQL queries, you think about:
- *What is a Customer?*
- *What are the rules that govern an Order?*
- *What happens when a Payment is processed?*

Hike provides ready‑made, highly type‑safe building blocks so you don’t have to write all of that plumbing yourself.

---

## Core Concepts at a Glance

| Concept | Simple Explanation | Why it helps you |
| :--- | :--- | :--- |
| **[Value Objects](guide/value-objects.md)** | Objects defined **only by their value** (e.g., `Money`, `Email`, `PhoneNumber`). | Eliminates “primitive obsession”, guarantees immutability, and auto‑validates data. |
| **[Entities & Aggregates](guide/entities-aggregates.md)** | Objects that have a unique identity (`Entity`) and clusters of objects that are managed together (`Aggregate`). | Safeguards business rules (invariants) and tracks important business happenings (Domain Events). |
| **[Rules, Commands & Invariants](guide/rules-commands.md)** | `@rule` predicates, `__invariants__` auto-checked on construction, `@command` for controlled mutations. | Enforces business rules at the boundary; prevents accidental mutations from bypassing aggregate logic. |
| **[Specifications](guide/specifications.md)** | Reusable business rules and query filters you can combine like `(age > 18) & (status == “active”)`. | Encapsulates complex querying logic so you can build expressive, composable rules. |
| **[Repositories & Unit of Work](guide/repositories-uow.md)** | Gateways for persisting data (`Repository`) and a transactional boundary for grouped saves (`Unit of Work`). | Keeps your business logic independent of the database and guarantees safe, atomic database operations. |
| **[SQLAlchemy Mappers](guide/sqlalchemy-mappers.md)** | Four mapper strategies: auto-inferred relational, auto-inferred flat, explicit ORM models, or explicit single-table. | Zero boilerplate for common cases; full control when you need it. |

---

## Beginner‑Friendly Resources

If you want to read more about the underlying concepts, these classic, easy‑to‑follow resources can help:

| Concept | Friendly Intro |
| :--- | :--- |
| [Domain‑Driven Design Fundamentals](https://dddcommunity.org/) | Community‑driven guide to DDD ideas and terminology. |
| [Value Objects Explained](https://martinfowler.com/bliki/ValueObject.html) | Martin Fowler’s clear walkthrough of the pattern. |
| [Repository Pattern Overview](https://martinfowler.com/eaaCatalog/repository.html) | Introductory article on why repositories matter. |
| [Unit of Work Basics](https://martinfowler.com/eaaCatalog/unitOfWork.html) | Simple explanation of transaction management. |
| [Specification Pattern](https://martinfowler.com/aps8/refactoringSpecification.html) | Quick primer on composable business rules. |

Ready to start building with Hike? Jump over to the **[Getting Started Guide](guide/getting-started.md)** to install the library and write your first domain model.