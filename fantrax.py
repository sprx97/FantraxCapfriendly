"""Download and parse Fantrax player data."""
import csv
import re
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import config
from models import Player

FANTRAX_LEAGUE_ID = "9bzjwovdmohkvwiv"
FANTRAX_EXPORT_URL = (
    "https://www.fantrax.com/fxpa/downloadPlayerStats"
    f"?leagueId={FANTRAX_LEAGUE_ID}&pageNumber=1&view=STATS&positionOrGroup=ALL"
    "&seasonOrProjection=SEASON_31l_YEAR_TO_DATE&timeframeTypeCode=YEAR_TO_DATE"
    "&transactionPeriod=17&miscDisplayType=1&sortType=SALARY"
    "&statusOrTeamFilter=ALL_TAKEN&scoringCategoryType=5&timeStartType=PERIOD_ONLY"
    "&schedulePageAdj=0&searchName=&datePlaying=ALL"
)

def _http_session() -> requests.Session:
    retry = Retry(
        total=5,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
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
