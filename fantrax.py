"""Download and parse Fantrax player data."""
import csv
import re
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import config
from models import CapHitPenalty, Player

FANTRAX_LEAGUE_ID = "9bzjwovdmohkvwiv"
FANTRAX_EXPORT_URL = (
    "https://www.fantrax.com/fxpa/downloadPlayerStats"
    f"?leagueId={FANTRAX_LEAGUE_ID}&pageNumber=1&view=STATS&positionOrGroup=ALL"
    "&seasonOrProjection=SEASON_31l_YEAR_TO_DATE&timeframeTypeCode=YEAR_TO_DATE"
    "&transactionPeriod=17&miscDisplayType=1&sortType=SALARY"
    "&statusOrTeamFilter=ALL_TAKEN&scoringCategoryType=5&timeStartType=PERIOD_ONLY"
    "&schedulePageAdj=0&searchName=&datePlaying=ALL"
)
FANTRAX_API_URL = "https://www.fantrax.com/fxpa/req"

def _http_session() -> requests.Session:
    retry = Retry(
        total=5,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "POST"),
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session

def _api_request(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    response = _http_session().post(
        FANTRAX_API_URL,
        params={"leagueId": FANTRAX_LEAGUE_ID},
        headers={
            "Cookie": config.FANTRAX_LOGIN_COOKIE,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        json={
            "msgs": messages,
            "uiv": 3,
            "refUrl": (
                "https://www.fantrax.com/fantasy/league/"
                f"{FANTRAX_LEAGUE_ID}/team/roster"
            ),
            "dt": 0,
            "at": 0,
            "tz": "Etc/UTC",
        },
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()
    responses = payload.get("responses")
    if not isinstance(responses, list) or len(responses) != len(messages):
        raise RuntimeError("Fantrax returned an unexpected API response")
    for item in responses:
        errors = item.get("errors")
        if errors:
            raise RuntimeError(f"Fantrax API error: {errors}")
    return responses

def _response_data(response: dict[str, Any]) -> dict[str, Any]:
    data = response.get("data")
    return data if isinstance(data, dict) else {}

def _penalty_start_year(value: str, default: int) -> int:
    match = re.search(r"(\d{2,4})\)?$", value or "")
    if not match:
        return default
    year = int(match.group(1))
    return 2000 + year if year < 100 else year

def _parse_signed_salary(value: str) -> int:
    amount = parse_salary(value)
    return -amount if "-" in value else amount

def download_cap_hit_penalties(season_start_year: int) -> list[CapHitPenalty]:
    """Download every fantasy team's active cap-hit penalties."""

    team_response = _api_request([{"method": "getFantasyTeams", "data": {}}])[0]
    teams = _response_data(team_response).get("fantasyTeams")
    if not isinstance(teams, list):
        raise RuntimeError("Fantrax did not return the league's fantasy teams")

    roster_responses = _api_request([
        {
            "method": "getTeamRosterInfo",
            "data": {"leagueId": FANTRAX_LEAGUE_ID, "teamId": team["id"]},
        }
        for team in teams
    ])
    penalties = []
    for team, roster_response in zip(teams, roster_responses):
        penalty_data = _response_data(roster_response).get("capHitPenaltyData") or {}
        for row in penalty_data.get("tableData") or []:
            scorer = row.get("scorer") or {}
            player_name = scorer.get("name")
            if not player_name:
                raise RuntimeError(
                    f"Fantrax returned a cap-hit penalty without a player for "
                    f"{team.get('shortName') or team.get('name')}"
                )
            penalties.append(CapHitPenalty(
                player_name=player_name,
                owner=team.get("shortName") or team["name"],
                amount=_parse_signed_salary(row.get("salaryAmount", "")),
                start_year=_penalty_start_year(
                    row.get("startPeriod", ""), season_start_year
                ),
                end_year=int(row["endingSeasonYear"]),
            ))
    return penalties

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
