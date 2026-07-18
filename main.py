"""Build and optionally publish a Fantrax player contract grid."""
from __future__ import annotations

import argparse
import atexit
import csv
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable, Protocol, Sequence
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import config

FANTRAX_LEAGUE_ID = "9bzjwovdmohkvwiv"
FANTRAX_EXPORT_URL = (
    "https://www.fantrax.com/fxpa/downloadPlayerStats"
    f"?leagueId={FANTRAX_LEAGUE_ID}&pageNumber=1&view=STATS&positionOrGroup=ALL"
    "&seasonOrProjection=SEASON_31l_YEAR_TO_DATE&timeframeTypeCode=YEAR_TO_DATE"
    "&transactionPeriod=17&miscDisplayType=1&sortType=SALARY"
    "&statusOrTeamFilter=ALL_TAKEN&scoringCategoryType=5&timeStartType=PERIOD_ONLY"
    "&schedulePageAdj=0&searchName=&datePlaying=ALL"
)
PROJECT_ROOT = Path(getattr(config, "PROJECT_ROOT", "") or Path(__file__).parent)
FANTRAX_EXPORT_FILE = PROJECT_ROOT / "outputs" / "fantrax_export.csv"
GRID_OUTPUT_FILE = PROJECT_ROOT / "outputs" / "player_contract_grid.csv"
DEFAULT_GRID_YEARS = 8

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

class ContractProvider(Protocol):
    def get_contracts(self, player: Player, season_start_year: int) -> ContractSnapshot:
        """Return current and not-yet-active contracts."""

class PlaceholderPuckPediaProvider:
    """Replace this provider when PuckPedia API access is available."""

    def get_contracts(self, player: Player, season_start_year: int) -> ContractSnapshot:
        return ContractSnapshot(
            ContractTerm(season_start_year, (player.salary, player.salary, player.salary))
        )

def current_season_start_year(today: date | None = None) -> int:
    today = today or date.today()
    return today.year if today.month >= 7 else today.year - 1

def season_label(start_year: int) -> str:
    return f"{start_year}-{str(start_year + 1)[-2:]}"

def puckpedia_player_url(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-")
    return f"https://puckpedia.com/player/{slug}"

def excel_hyperlink_formula(name: str) -> str:
    """Create an Excel-safe hyperlink formula displaying the player's name."""

    escaped_name = name.replace('"', '""')
    url = puckpedia_player_url(name)
    return f'=HYPERLINK("{url}","{escaped_name}")'

def parse_salary(value: str) -> int:
    digits = re.sub(r"[^0-9]", "", value or "")
    if not digits:
        raise ValueError(f"Invalid Fantrax salary: {value!r}")
    return int(digits)

def _first_present(row: dict[str, str], *names: str) -> str:
    for name in names:
        if name in row and row[name] is not None:
            return row[name].strip()
    raise ValueError(f"Fantrax CSV is missing required column(s): {', '.join(names)}")

def load_fantrax_players(path: Path) -> list[Player]:
    players = []
    with path.open(newline="", encoding="utf-8-sig") as csvfile:
        for row in csv.DictReader(csvfile):
            name = _first_present(row, "Player", "Name")
            if not name:
                continue
            age = _first_present(row, "Age")
            players.append(Player(
                name=name,
                team=_first_present(row, "Team"),
                position=_first_present(row, "Position", "Pos").replace(",", "/"),
                owner=_first_present(row, "Status"),
                roster_status=_first_present(row, "Roster Status"),
                age=int(age) if age else None,
                salary=parse_salary(_first_present(row, "Salary")),
            ))
    return players

def build_contract_grid(
    players: Iterable[Player],
    provider: ContractProvider,
    season_start_year: int,
    grid_years: int = DEFAULT_GRID_YEARS,
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
                    raise ValueError(f"Overlapping contracts returned for {player.name} in {year}")
                cap_hits[year] = cap_hit
        rows.append([
            excel_hyperlink_formula(player.name),
            player.team, player.position, player.owner, player.roster_status,
            player.age if player.age is not None else "",
            *(cap_hits.get(year, "") for year in seasons),
        ])
    roster_status_order = {
        "active": 0,
        "reserve": 1,
        "reserved": 1,
        "inj res": 2,
        "injured reserve": 2,
        "minors": 3,
    }
    owner_column = headers.index("Owner")
    roster_status_column = headers.index("Roster Status")
    first_contract_column = len(headers) - grid_years

    def row_sort_key(row: Sequence[str | int]) -> tuple:
        first_cap_hit = row[first_contract_column]
        cap_hit_sort = -first_cap_hit if isinstance(first_cap_hit, int) else float("inf")
        return (
            str(row[owner_column]).casefold(),
            roster_status_order.get(
                str(row[roster_status_column]).casefold(),
                len(roster_status_order),
            ),
            cap_hit_sort,
            str(row[0]).casefold(),
        )

    rows.sort(key=row_sort_key)
    return headers, rows

def write_grid_csv(path: Path, headers: Sequence[str], rows: Iterable[Sequence[object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(headers)
        writer.writerows(rows)

def _http_session() -> requests.Session:
    retry = Retry(total=5, backoff_factor=1,
                  status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=("GET", "POST", "PATCH", "DELETE"))
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session

def download_fantrax_csv(path: Path) -> None:
    response = _http_session().get(
        FANTRAX_EXPORT_URL,
        headers={"Cookie": config.FANTRAX_LOGIN_COOKIE},
        timeout=60,
    )
    response.raise_for_status()
    if response.headers.get("content-type", "").startswith("application/json"):
        raise RuntimeError(f"Fantrax returned an error instead of CSV: {response.text}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(response.text, encoding="utf-8")

# This is hacky and uses MDH's token cache, but hey it works I guess.
def _acquire_azure_token() -> str:
    try:
        import msal
    except ImportError as exc:
        raise RuntimeError("Azure upload requires the 'msal' package") from exc
    cache_path = Path(getattr(
        config,
        "AZURE_TOKEN_CACHE",
        PROJECT_ROOT.parent / "mdh-hockey" / "mdhhockey" / "response_cache" / "cache.bin",
    ))
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache = msal.SerializableTokenCache()
    if cache_path.exists():
        cache.deserialize(cache_path.read_text(encoding="utf-8"))

    def save_cache() -> None:
        if cache.has_state_changed:
            cache_path.write_text(cache.serialize(), encoding="utf-8")

    atexit.register(save_cache)
    scopes = getattr(config, "AZURE_SCOPES",
                     ["Files.ReadWrite.All", "Sites.ReadWrite.All", "User.Read"])
    app = msal.PublicClientApplication(
        config.AZURE_CLIENT_ID,
        authority=getattr(config, "AZURE_AUTHORITY",
                          "https://login.microsoftonline.com/consumers"),
        token_cache=cache,
    )
    accounts = app.get_accounts(username=config.AZURE_USER)
    result = (
        app.acquire_token_silent(scopes, account=accounts[0], force_refresh=True)
        if accounts else None
    )
    if not result:
        print("Could not renew token, need interactive acquisition.")
        result = app.acquire_token_interactive(scopes=scopes)
    if "access_token" not in result:
        raise RuntimeError(result.get("error_description", "Azure authentication failed"))
    return result["access_token"]

def _excel_column_name(number: int) -> str:
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result

class ExcelGraphPublisher:
    def __init__(
        self,
        token: str,
        drive_id: str,
        item_id: str,
        worksheet: str,
        table_name: str = "",
    ):
        self.session = _http_session()
        self.session.headers.update({"Authorization": f"Bearer {token}"})
        self.table_name = table_name
        self.workbook_url = (
            f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{item_id}"
            "/workbook"
        )
        self.sheet_url = (
            f"{self.workbook_url}/worksheets/{quote(worksheet, safe='')}"
        )

    def _request(
        self,
        method: str,
        suffix: str,
        workbook_scope: bool = False,
        **kwargs,
    ) -> requests.Response:
        base_url = self.workbook_url if workbook_scope else self.sheet_url
        response = self.session.request(
            method, f"{base_url}/{suffix}", timeout=60, **kwargs
        )
        if not response.ok:
            raise RuntimeError(
                f"Microsoft Graph returned {response.status_code} for {suffix}: {response.text}"
            )
        return response

    def _get_table(self) -> dict | None:
        tables = self._request("GET", "tables").json().get("value", [])
        if self.table_name:
            matches = [
                table for table in tables
                if table.get("name", "").casefold() == self.table_name.casefold()
            ]
            if not matches:
                raise RuntimeError(
                    f"Excel table {self.table_name!r} was not found in the worksheet"
                )
            return matches[0]
        if len(tables) > 1:
            raise RuntimeError(
                "The worksheet contains multiple tables; set AZURE_TABLE_NAME in config.py"
            )
        return tables[0] if tables else None

    def replace_grid(self, values: Sequence[Sequence[object]]) -> None:
        if not values or not values[0]:
            raise ValueError("Cannot publish an empty grid")
        destination = f"A1:{_excel_column_name(len(values[0]))}{len(values)}"
        table = self._get_table()
        if table:
            table_id = quote(table["id"], safe="")
            body_range = self._request(
                "GET", f"tables/{table_id}/dataBodyRange"
            ).json()
            existing_rows = body_range.get("rowCount", len(body_range.get("values", [])))
            desired_rows = len(values) - 1
            if desired_rows > existing_rows:
                blank_rows = [
                    [""] * len(values[0])
                    for _ in range(desired_rows - existing_rows)
                ]
                self._request(
                    "POST",
                    f"tables/{table_id}/rows",
                    json={"index": None, "values": blank_rows},
                )
            elif desired_rows < existing_rows:
                for row_index in range(existing_rows - 1, desired_rows - 1, -1):
                    self._request(
                        "DELETE",
                        f"tables/{table_id}/rows/{row_index}",
                    )
            self._request(
                "PATCH",
                f"range(address='{destination}')",
                json={"formulas": values},
            )
            return

        used = self._request("GET", "usedRange(valuesOnly=true)").json()
        if used.get("values"):
            address = used["address"].split("!", 1)[-1]
            self._request("POST", f"range(address='{address}')/clear",
                          json={"applyTo": "Contents"})
        # The Name column contains HYPERLINK formulas. Sending the whole grid
        # through the formulas property preserves those links while ordinary
        # strings and numbers remain literal cell values.
        self._request("PATCH", f"range(address='{destination}')",
                      json={"formulas": values})

def publish_grid_to_excel(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> None:
    required = ("AZURE_DRIVE_ID", "AZURE_WORKBOOK_ITEM_ID", "AZURE_WORKSHEET_NAME")
    missing = [name for name in required if not getattr(config, name, "")]
    if missing:
        raise RuntimeError(f"Azure upload is not configured; set {', '.join(missing)}")
    ExcelGraphPublisher(
        _acquire_azure_token(), config.AZURE_DRIVE_ID,
        config.AZURE_WORKBOOK_ITEM_ID, config.AZURE_WORKSHEET_NAME,
        getattr(config, "AZURE_TABLE_NAME", ""),
    ).replace_grid([list(headers), *[list(row) for row in rows]])

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path,
                        help="Use an existing Fantrax CSV instead of downloading")
    parser.add_argument("--output", type=Path, default=GRID_OUTPUT_FILE)
    parser.add_argument("--years", type=int, default=DEFAULT_GRID_YEARS)
    parser.add_argument("--season-start-year", type=int,
                        default=current_season_start_year())
    parser.add_argument("--upload", "--update", dest="upload", action="store_true",
                        help="Publish the grid to Excel via Graph")
    return parser.parse_args()

def main() -> None:
    args = parse_args()
    input_path = args.input or FANTRAX_EXPORT_FILE
    if args.input is None:
        print("Downloading Fantrax player data...")
        download_fantrax_csv(input_path)
    players = load_fantrax_players(input_path)
    headers, rows = build_contract_grid(
        players, PlaceholderPuckPediaProvider(),
        args.season_start_year, args.years,
    )
    write_grid_csv(args.output, headers, rows)
    print(f"Wrote {len(rows)} players to {args.output}")
    if args.upload:
        publish_grid_to_excel(headers, rows)
        print(f"Published grid to worksheet {config.AZURE_WORKSHEET_NAME!r}")

if __name__ == "__main__":
    main()
