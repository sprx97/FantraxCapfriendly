"""Build and optionally publish a Fantrax player contract grid."""
import argparse
from pathlib import Path

import config
from capwages import CachedCapWagesProvider, refresh_capwages_cache
from contract_grid import (
    DEFAULT_GRID_YEARS,
    build_cap_summary,
    build_contract_grid,
    current_season_start_year,
    write_grid_csv,
)
from excel import publish_grid_to_excel
from fantrax import (
    download_cap_hit_penalties,
    download_fantrax_csv,
    load_fantrax_players,
)

PROJECT_ROOT = Path(getattr(config, "PROJECT_ROOT", "") or Path(__file__).parent)
FANTRAX_EXPORT_FILE = PROJECT_ROOT / "outputs" / "fantrax_export.csv"
GRID_OUTPUT_FILE = PROJECT_ROOT / "outputs" / "player_contract_grid.csv"
CAPWAGES_CACHE_FILE = PROJECT_ROOT / ".cache" / "capwages_contracts.json"

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        help="Use an existing Fantrax CSV instead of downloading",
    )
    parser.add_argument("--output", type=Path, default=GRID_OUTPUT_FILE)
    parser.add_argument(
        "--capwages-cache",
        type=Path,
        default=CAPWAGES_CACHE_FILE,
        help="Path to the cached CapWages contract JSON",
    )
    parser.add_argument(
        "--refresh-capwages",
        action="store_true",
        help="Refresh the contract cache by scraping all 32 CapWages team pages",
    )
    parser.add_argument("--years", type=int, default=DEFAULT_GRID_YEARS)
    parser.add_argument(
        "--season-start-year",
        type=int,
        default=current_season_start_year(),
    )
    parser.add_argument(
        "--upload",
        "--update",
        dest="upload",
        action="store_true",
        help="Publish the grid to Excel via Graph",
    )
    return parser.parse_args()

def main() -> None:
    args = parse_args()
    input_path = args.input or FANTRAX_EXPORT_FILE
    if args.input is None:
        print("Downloading Fantrax player data...")
        download_fantrax_csv(input_path)
    players = load_fantrax_players(input_path)
    if args.refresh_capwages:
        print("Refreshing CapWages contract cache from 32 team pages...")
        cached_players = refresh_capwages_cache(
            args.capwages_cache,
            args.season_start_year,
        )
        print(f"Cached {cached_players} CapWages players")
    contract_provider = CachedCapWagesProvider(args.capwages_cache)
    print("Downloading Fantrax cap-hit penalties...")
    cap_hit_penalties = download_cap_hit_penalties(args.season_start_year)
    headers, rows = build_contract_grid(
        players,
        contract_provider,
        args.season_start_year,
        args.years,
        cap_hit_penalties,
    )
    write_grid_csv(args.output, headers, rows)
    print(f"Wrote {len(rows)} rows to {args.output}")
    if contract_provider.unmatched_names:
        print(
            f"No CapWages match for {len(contract_provider.unmatched_names)} "
            "Fantrax players; their contract years were left blank"
        )
    if args.upload:
        summary_headers, summary_rows = build_cap_summary(
            headers,
            rows,
            config.AZURE_WORKSHEET_NAME,
        )
        publish_grid_to_excel(
            headers,
            rows,
            summary_headers,
            summary_rows,
        )
        summary_sheet = getattr(
            config,
            "AZURE_CAP_SUMMARY_WORKSHEET_NAME",
            "Cap Summary",
        )
        print(
            f"Published grid to worksheet {config.AZURE_WORKSHEET_NAME!r} "
            f"and {len(summary_rows)} cap-summary rows to {summary_sheet!r}"
        )

if __name__ == "__main__":
    main()
