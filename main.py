"""Build and optionally publish a Fantrax player contract grid."""
import argparse
from pathlib import Path

import config
from contract_grid import (
    DEFAULT_GRID_YEARS,
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
from puckpedia import PlaceholderPuckPediaProvider

PROJECT_ROOT = Path(getattr(config, "PROJECT_ROOT", "") or Path(__file__).parent)
FANTRAX_EXPORT_FILE = PROJECT_ROOT / "outputs" / "fantrax_export.csv"
GRID_OUTPUT_FILE = PROJECT_ROOT / "outputs" / "player_contract_grid.csv"

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        help="Use an existing Fantrax CSV instead of downloading",
    )
    parser.add_argument("--output", type=Path, default=GRID_OUTPUT_FILE)
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
    print("Downloading Fantrax cap-hit penalties...")
    cap_hit_penalties = download_cap_hit_penalties(args.season_start_year)
    headers, rows = build_contract_grid(
        players,
        PlaceholderPuckPediaProvider(),
        args.season_start_year,
        args.years,
        cap_hit_penalties,
    )
    write_grid_csv(args.output, headers, rows)
    print(f"Wrote {len(rows)} rows to {args.output}")
    if args.upload:
        publish_grid_to_excel(headers, rows)
        print(f"Published grid to worksheet {config.AZURE_WORKSHEET_NAME!r}")

if __name__ == "__main__":
    main()
