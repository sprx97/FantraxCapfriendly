"""Cached CapWages contract provider."""
import json
import re
import time
import unicodedata
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from models import ContractSnapshot, ContractTerm, Player

CAPWAGES_URL = "https://capwages.com"
CACHE_VERSION = 1
# Add future Fantrax spelling variants here as "Fantrax": "CapWages".
PLAYER_NAME_ALIASES: dict[str, str] = {
    "Egor Chinakhov": "Yegor Chinakhov",
}


class _NextDataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._capture = False
        self._chunks: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag == "script" and dict(attrs).get("id") == "__NEXT_DATA__":
            self._capture = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._capture:
            self._capture = False

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._chunks.append(data)

    @property
    def data(self) -> str:
        return "".join(self._chunks)


def _http_session() -> requests.Session:
    retry = Retry(
        total=5,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    session = requests.Session()
    session.headers.update({
        "User-Agent": "kkupdl/1.0 (personal fantasy hockey contract grid)",
    })
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def _fetch_page_props(session: requests.Session, url: str) -> dict[str, Any]:
    response = session.get(url, timeout=60)
    response.raise_for_status()
    parser = _NextDataParser()
    parser.feed(response.text)
    if not parser.data:
        raise RuntimeError(f"CapWages page did not contain __NEXT_DATA__: {url}")
    try:
        return json.loads(parser.data)["props"]["pageProps"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"CapWages returned unexpected page data: {url}") from exc


def _slugify(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-")


def _canonical_player_name(name: str) -> str:
    return PLAYER_NAME_ALIASES.get(name, name)


def capwages_player_url(name: str) -> str:
    slug = _slugify(_canonical_player_name(name))
    return f"{CAPWAGES_URL}/players/{slug}"


def _display_name(name: str) -> str:
    if "," not in name:
        return name.strip()
    last, first = name.split(",", 1)
    return f"{first.strip()} {last.strip()}"


def _parse_money(value: str) -> int | None:
    digits = re.sub(r"[^0-9]", "", value or "")
    if not digits:
        return None
    amount = int(digits)
    return -amount if "-" in value else amount


def _iter_grouped_players(group: object) -> Iterable[dict[str, Any]]:
    if isinstance(group, list):
        for player in group:
            if isinstance(player, dict):
                yield player
    elif isinstance(group, dict):
        for players in group.values():
            yield from _iter_grouped_players(players)


def _player_cache_record(
    player: dict[str, Any],
    fallback_team: str,
    season_start_year: int,
) -> dict[str, Any] | None:
    slug = player.get("slug")
    raw_name = player.get("name")
    if not slug or not raw_name:
        return None
    cap_hits: dict[int, int] = {}
    for contract in player.get("contracts") or []:
        for detail in contract.get("details") or []:
            season = detail.get("season", "")
            match = re.fullmatch(r"(\d{4})-\d{2}", season)
            cap_hit = _parse_money(detail.get("capHit", ""))
            if match and cap_hit is not None:
                cap_hits.setdefault(int(match.group(1)), cap_hit)
    future_hits = {
        str(year): cap_hits[year]
        for year in sorted(cap_hits)
        if year >= season_start_year
    }
    record = {
        "name": _display_name(raw_name),
        "slug": slug,
        "team": player.get("currentTeamTricode") or fallback_team,
        "years_remaining": len(future_hits),
        "cap_hits": future_hits,
    }
    aliases = [
        alias
        for alias, canonical in PLAYER_NAME_ALIASES.items()
        if _slugify(canonical) == slug
    ]
    if aliases:
        record["aliases"] = aliases
    return record


def refresh_capwages_cache(
    cache_path: Path,
    season_start_year: int,
    request_delay: float = 0.2,
) -> int:
    """Scrape all current team pages and atomically replace the JSON cache."""

    session = _http_session()
    home = _fetch_page_props(session, f"{CAPWAGES_URL}/")
    teams = home.get("teamsData")
    if not isinstance(teams, list) or len(teams) != 32:
        raise RuntimeError("CapWages did not return exactly 32 teams")

    records: dict[tuple[str, str], dict[str, Any]] = {}
    for index, team in enumerate(teams):
        team_slug = team.get("url")
        team_code = team.get("tricode")
        if not team_slug or not team_code:
            raise RuntimeError("CapWages returned a team without a slug or tricode")
        page = _fetch_page_props(
            session,
            f"{CAPWAGES_URL}/teams/{team_slug}",
        )
        data = page.get("data") or {}
        player_groups = [
            data.get("roster"),
            data.get("inactive"),
            data.get("non-roster"),
            page.get("reserves"),
        ]
        for group in player_groups:
            for player in _iter_grouped_players(group):
                record = _player_cache_record(
                    player,
                    team_code,
                    season_start_year,
                )
                if record is not None:
                    key = (record["slug"], record["team"])
                    existing = records.get(key)
                    if existing is None or len(record["cap_hits"]) > len(
                        existing["cap_hits"]
                    ):
                        records[key] = record
        if request_delay and index < len(teams) - 1:
            time.sleep(request_delay)

    payload = {
        "version": CACHE_VERSION,
        "source": CAPWAGES_URL,
        "refreshed_at": datetime.now(timezone.utc).isoformat(),
        "season_start_year": season_start_year,
        "teams": len(teams),
        "players": sorted(
            records.values(),
            key=lambda item: (item["name"].casefold(), item["team"]),
        ),
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(cache_path)
    return len(records)


class CachedCapWagesProvider:
    def __init__(self, cache_path: Path):
        if not cache_path.exists():
            raise FileNotFoundError(
                f"CapWages cache not found at {cache_path}. "
                "Run main.py with --refresh-capwages first."
            )
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        if payload.get("version") != CACHE_VERSION:
            raise RuntimeError(
                "Unsupported CapWages cache version; refresh the cache"
            )
        players = payload.get("players")
        if not isinstance(players, list):
            raise RuntimeError("CapWages cache does not contain a player list")
        self._by_slug: dict[str, list[dict[str, Any]]] = {}
        self._by_name: dict[str, list[dict[str, Any]]] = {}
        self._by_team_last_name: dict[
            tuple[str, str], list[dict[str, Any]]
        ] = {}
        for entry in players:
            self._by_slug.setdefault(entry["slug"], []).append(entry)
            names = (entry["name"], *entry.get("aliases", ()))
            for name in names:
                self._by_name.setdefault(_slugify(name), []).append(entry)
            name_key = _slugify(entry["name"])
            last_name = name_key.rsplit("-", 1)[-1]
            self._by_team_last_name.setdefault(
                (entry["team"], last_name), []
            ).append(entry)
        self.unmatched_names: set[str] = set()

    @staticmethod
    def _choose_entry(
        entries: Iterable[dict[str, Any]],
        team: str,
    ) -> dict[str, Any] | None:
        entries = list(entries)
        team_matches = [entry for entry in entries if entry.get("team") == team]
        if len(team_matches) == 1:
            return team_matches[0]
        if len(entries) == 1:
            return entries[0]
        return None

    def _find_player(self, player: Player) -> dict[str, Any] | None:
        key = _slugify(_canonical_player_name(player.name))
        entry = self._choose_entry(self._by_slug.get(key, ()), player.team)
        if entry is not None:
            return entry
        entry = self._choose_entry(self._by_name.get(key, ()), player.team)
        if entry is not None:
            return entry
        first_name = key.split("-", 1)[0]
        last_name = key.rsplit("-", 1)[-1]
        aliases = self._by_team_last_name.get((player.team, last_name), ())
        initial_matches = [
            candidate
            for candidate in aliases
            if _slugify(candidate["name"]).startswith(first_name[:1])
        ]
        return initial_matches[0] if len(initial_matches) == 1 else None

    def get_contracts(
        self,
        player: Player,
        season_start_year: int,
    ) -> ContractSnapshot:
        entry = self._find_player(player)
        if entry is None:
            self.unmatched_names.add(player.name)
            return ContractSnapshot(ContractTerm(season_start_year, ()))

        cap_hits = {
            int(year): int(amount)
            for year, amount in entry.get("cap_hits", {}).items()
            if int(year) >= season_start_year
        }
        if not cap_hits:
            return ContractSnapshot(ContractTerm(season_start_year, ()))

        terms: list[ContractTerm] = []
        term_years: list[int] = []
        term_hits: list[int] = []
        for year in sorted(cap_hits):
            if term_years and year != term_years[-1] + 1:
                terms.append(ContractTerm(term_years[0], tuple(term_hits)))
                term_years = []
                term_hits = []
            term_years.append(year)
            term_hits.append(cap_hits[year])
        terms.append(ContractTerm(term_years[0], tuple(term_hits)))
        return ContractSnapshot(terms[0], tuple(terms[1:]))
