"""Build, sort, and save the player contract grid."""
import csv
from datetime import date
from pathlib import Path
from typing import Iterable, Sequence

from capwages import capwages_player_url
from models import CapHitPenalty, ContractProvider, Player

DEFAULT_GRID_YEARS = 8
ROSTER_STATUS_ORDER = {
    "active": 0,
    "reserve": 1,
    "reserved": 1,
    "inj res": 2,
    "injured reserve": 2,
    "minors": 3,
}

def current_season_start_year(today: date | None = None) -> int:
    today = today or date.today()
    return today.year if today.month >= 7 else today.year - 1

def season_label(start_year: int) -> str:
    return f"{start_year}-{str(start_year + 1)[-2:]}"

def excel_hyperlink_formula(name: str) -> str:
    """Create an Excel-safe hyperlink formula displaying the player's name."""

    escaped_name = name.replace('"', '""')
    url = capwages_player_url(name)
    return f'=HYPERLINK("{url}","{escaped_name}")'

def build_contract_grid(
    players: Iterable[Player],
    provider: ContractProvider,
    season_start_year: int,
    grid_years: int = DEFAULT_GRID_YEARS,
    cap_hit_penalties: Iterable[CapHitPenalty] = (),
) -> tuple[list[str], list[list[str | int]]]:
    if grid_years < 1:
        raise ValueError("grid_years must be at least 1")
    seasons = list(range(season_start_year, season_start_year + grid_years))
    headers = [
        "Name", "Team", "Position", "Owner", "Roster Status", "Age",
        *(season_label(year) for year in seasons),
    ]
    rows = []
    for player in players:
        snapshot = provider.get_contracts(player, season_start_year)
        cap_hits = {}
        for term in (snapshot.current, *snapshot.future):
            for offset, cap_hit in enumerate(term.cap_hits):
                year = term.start_year + offset
                if year in cap_hits:
                    raise ValueError(
                        f"Overlapping contracts returned for {player.name} in {year}"
                    )
                cap_hits[year] = cap_hit
        rows.append([
            excel_hyperlink_formula(player.name),
            player.team,
            player.position,
            player.owner,
            player.roster_status,
            player.age if player.age is not None else "",
            *(cap_hits.get(year, "") for year in seasons),
        ])
    for penalty in cap_hit_penalties:
        rows.append([
            f"{penalty.player_name} Retention",
            "",
            "",
            penalty.owner,
            "Cap Hit",
            "",
            *(
                penalty.amount
                if penalty.start_year <= year <= penalty.end_year
                else ""
                for year in seasons
            ),
        ])
    owner_column = headers.index("Owner")
    roster_status_column = headers.index("Roster Status")
    first_contract_column = len(headers) - grid_years

    def row_sort_key(row: Sequence[str | int]) -> tuple:
        first_cap_hit = row[first_contract_column]
        cap_hit_sort = -first_cap_hit if isinstance(first_cap_hit, int) else float("inf")
        return (
            str(row[owner_column]).casefold(),
            ROSTER_STATUS_ORDER.get(
                str(row[roster_status_column]).casefold(),
                len(ROSTER_STATUS_ORDER),
            ),
            cap_hit_sort,
            str(row[0]).casefold(),
        )

    rows.sort(key=row_sort_key)
    return headers, rows

def write_grid_csv(
    path: Path,
    headers: Sequence[str],
    rows: Iterable[Sequence[object]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(headers)
        writer.writerows(rows)
