"""BusinessClock: the one place `date.today()` is allowed to be called in
this codebase's Agent/Tool layer. Repositories and Services never call it
themselves (see order_repository.py / shipment_repository.py /
order_context_service.py docstrings) -- every time-relative computation
takes `as_of_date` as an explicit parameter, and BusinessClock is where a
Tool obtains that value before passing it down.

Tests inject FixedBusinessClock so "is this overdue" assertions don't
silently start failing the day after they're written.
"""

from dataclasses import dataclass
from datetime import date
from typing import Protocol


class BusinessClock(Protocol):
    def today(self) -> date: ...


@dataclass(frozen=True)
class RealBusinessClock:
    def today(self) -> date:
        return date.today()


@dataclass(frozen=True)
class FixedBusinessClock:
    """For tests and for a possible future 'historical replay' scenario
    mode -- never used by default in production code paths."""
    fixed_date: date

    def today(self) -> date:
        return self.fixed_date


def get_business_clock() -> BusinessClock:
    return RealBusinessClock()
