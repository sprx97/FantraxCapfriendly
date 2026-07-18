"""Shared player and contract data models."""
from dataclasses import dataclass
from typing import Protocol

@dataclass(frozen=True)
class Player:
    name: str
    team: str
    position: str
    owner: str
    roster_status: str
    age: int | None
    salary: int

@dataclass(frozen=True)
class ContractTerm:
    start_year: int
    cap_hits: tuple[int, ...]

    @property
    def years(self) -> int:
        return len(self.cap_hits)

    @property
    def end_year(self) -> int:
        return self.start_year + self.years

@dataclass(frozen=True)
class ContractSnapshot:
    current: ContractTerm
    future: tuple[ContractTerm, ...] = ()

@dataclass(frozen=True)
class CapHitPenalty:
    player_name: str
    owner: str
    amount: int
    start_year: int
    end_year: int

class ContractProvider(Protocol):
    def get_contracts(
        self,
        player: Player,
        season_start_year: int,
    ) -> ContractSnapshot:
        """Return current and not-yet-active contracts."""
