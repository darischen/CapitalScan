"""The handler layer's exception hierarchy.

Every one of these is a *refusal*, never a repair. A handler that cannot
answer the question it was asked raises rather than returning a plausible
empty shape, because an empty shape is indistinguishable from "nothing
matched" and the two mean opposite things to a reader.

The one deliberate exception is `Suppressed` and `NotFound` in
`handlers/types.py`. Those are *answers* — "the cell exists and has too
little data" and "no prediction exists for this input" are both true
statements about the database, so they are return values. "You asked for
holdout" and "`dd_bucket='0-15'` is not a bucket" are not statements about
the database at all, so they raise.

Session 16 maps each of these to a distinct MCP protocol error, so the
hierarchy is a wire contract as much as a Python one. Adding a subclass is
a wire change.
"""

from __future__ import annotations


class HandlerError(Exception):
    """Base for everything this layer raises deliberately.

    Session 16's error mapping catches this and nothing wider: an
    `AttributeError` escaping a handler is a bug and should surface as one,
    not be laundered into a tidy protocol error.
    """


class InvalidEnum(HandlerError):
    """A closed-enum argument received a value outside its domain.

    The message always names the valid values. ADR 074 closes these enums
    precisely so a typo fails loudly, and a failure that does not say what
    was expected costs the caller a round trip to find out.
    """


class HoldoutRequested(InvalidEnum):
    """`split='holdout'` — refused everywhere, unconditionally.

    A subclass of `InvalidEnum` because that is what it is at the type
    level, and its own class because the refusal is a project invariant
    (ADR 019, ADR 033) rather than a domain check. `test_holdout_firewall`
    guards the database; this guards the only layer that could ask it.

    Holdout is evaluated exactly once, at the end, and published whatever
    it says. A serving layer that could read it early is a serving layer
    that has already spent it.
    """


class DateOutOfWindow(HandlerError):
    """A date outside the ingested bar window.

    The message names the window. Returning an empty result instead would
    be a lie by omission: "no events on 1987-10-19" and "no bars exist for
    1987 at all" are different facts and the caller cannot tell them apart
    from an empty list.
    """


class NotConfigured(HandlerError):
    """`capitalscan.default_config_hash` is unset on the database.

    `v_events` and `compute.scan` both return empty in this state, which is
    correct for a batch job and wrong for a serving layer: a screener
    showing zero rows every day looks like a quiet market. Raising makes
    the misconfiguration visible on the first call.
    """


class ResponseInvalid(HandlerError):
    """The response validator refused an outbound result.

    Raised by `handlers/validate.py`, never by a query. If this reaches a
    caller it means a handler built a statistical claim that violates
    invariant 8, which is a defect in this layer and not in the request.
    """
