"""Layer 2: the capability contract every reader declares, and the mesh that routes by it.

The measured problem
--------------------
Adding a format today means editing four files, and none of them says so:

    ir.py        one more `if probe.family is Family.X` branch
    delivery.py  one more entry in a frozenset of family names
    verify.py    one more check function and one more header test
    tools.py     one more handler and dispatch line

Four edits, no single place that declares "this format supports these
operations". A new adapter cannot be checked for completeness, and a caller
cannot ask "can anything here query a table?" without knowing every format by
name. That is the coupling the plan's layer 2 exists to remove.

The contract
------------
An :class:`Adapter` declares four things and nothing else:

* ``family`` / ``formats``  -- what it claims to read
* ``operations``            -- what it can do with those bytes
* ``describe``              -- how to build the IR (the layer-3 handoff)
* ``verify``                -- how to prove the output (the layer-5 handoff)

A registry maps a probe to the adapter that claims it. The routing code asks
the registry instead of enumerating families, so a new format is a new module
that calls :func:`register` -- and the router, the delivery planner and the
verifier all learn about it at once, or refuse it explicitly.

Why registration is explicit rather than an import side-effect
--------------------------------------------------------------
A module that registers itself on import is untestable in isolation and makes
import order load-bearing. The registry starts empty and the shipped adapters
register through one function, :func:`default_registry`, which a test can call
without touching global state.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from .ingest import Family, Probe


class Operation:
    """The verbs an adapter may claim. A closed set, so a typo cannot look like a capability."""

    INSPECT = "inspect"
    SLICE = "slice"
    QUERY = "query"
    PATCH = "patch"
    RENDER = "render"
    COMPILE = "compile"


ALL_OPERATIONS = frozenset(
    {
        Operation.INSPECT,
        Operation.SLICE,
        Operation.QUERY,
        Operation.PATCH,
        Operation.RENDER,
        Operation.COMPILE,
    }
)


@dataclass(frozen=True)
class Adapter:
    """One reader's declaration: what it claims, what it can do, how it hands off."""

    #: Stable identifier, used in reports and in the mesh's index.
    name: str
    #: The families this adapter serves.
    families: frozenset[Family]
    #: Concrete format names, when the adapter is narrower than its family.
    #: Empty means "every format in those families".
    formats: frozenset[str] = field(default_factory=frozenset)
    #: Verbs from :class:`Operation`. The mesh only routes to an adapter that
    #: claims the verb the caller asked for.
    operations: frozenset[str] = field(default_factory=frozenset)

    def claims(self, probe: Probe) -> bool:
        """Whether this adapter is responsible for the probed bytes."""
        if probe.family not in self.families:
            return False
        return not self.formats or probe.format in self.formats

    def supports(self, operation: str) -> bool:
        """Whether this adapter can perform the named operation."""
        return operation in self.operations


class UnknownOperationError(ValueError):
    """Raised when a caller asks for a verb outside the closed set."""


class Registry:
    """The mesh: adapters declare, the router asks. No family list lives here."""

    def __init__(self) -> None:
        self._adapters: list[Adapter] = []

    def register(self, adapter: Adapter) -> None:
        """Add one adapter, refusing a duplicate name so a reload cannot shadow one."""
        if any(existing.name == adapter.name for existing in self._adapters):
            raise ValueError(f"an adapter named {adapter.name!r} is already registered")
        unknown = adapter.operations - ALL_OPERATIONS
        if unknown:
            raise UnknownOperationError(
                f"{adapter.name} claims unknown operation(s): {sorted(unknown)}"
            )
        self._adapters.append(adapter)

    @property
    def adapters(self) -> tuple[Adapter, ...]:
        """Every registered adapter, in registration order."""
        return tuple(self._adapters)

    def for_probe(self, probe: Probe) -> Adapter | None:
        """The first adapter that claims these bytes, or ``None``.

        First-match is deliberate: an adapter for ``docx`` must be registered
        before the generic ZIP adapter so the specific reader wins. Registration
        order is the only ordering rule, and it is written down here rather than
        being an accident of file layout.
        """
        for adapter in self._adapters:
            if adapter.claims(probe):
                return adapter
        return None

    def providers(self, operation: str) -> tuple[Adapter, ...]:
        """Every adapter that claims an operation -- what a caller can ask for."""
        if operation not in ALL_OPERATIONS:
            raise UnknownOperationError(f"{operation!r} is not an operation; see Operation")
        return tuple(adapter for adapter in self._adapters if adapter.supports(operation))

    def coverage(self) -> dict[str, int]:
        """How many adapters claim each verb. Used by the tests, and by CI."""
        return {operation: len(self.providers(operation)) for operation in sorted(ALL_OPERATIONS)}


def _text_adapter() -> Adapter:
    return Adapter(
        name="text",
        families=frozenset({Family.TEXT}),
        operations=frozenset({Operation.INSPECT, Operation.SLICE, Operation.PATCH}),
    )


def _sqlite_adapter() -> Adapter:
    return Adapter(
        name="sqlite",
        families=frozenset({Family.DATABASE}),
        formats=frozenset({"sqlite"}),
        operations=frozenset({Operation.INSPECT, Operation.QUERY, Operation.SLICE}),
    )


def _parquet_adapter() -> Adapter:
    return Adapter(
        name="parquet",
        families=frozenset({Family.COLUMNAR}),
        formats=frozenset({"parquet"}),
        operations=frozenset({Operation.INSPECT, Operation.SLICE}),
    )


def _archive_adapter() -> Adapter:
    return Adapter(
        name="archive",
        families=frozenset({Family.ZIP, Family.ARCHIVE}),
        operations=frozenset({Operation.INSPECT, Operation.SLICE}),
    )


def default_registry() -> Registry:
    """The adapters this build ships, registered in specific-before-general order."""
    registry = Registry()
    for factory in (_sqlite_adapter, _parquet_adapter, _text_adapter, _archive_adapter):
        registry.register(factory())
    return registry


#: Type of a factory a caller may register without importing this module's internals.
AdapterFactory = Callable[[], Adapter]
