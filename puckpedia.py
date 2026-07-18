"""PuckPedia player links and contract providers."""
import re
import unicodedata

from models import ContractSnapshot, ContractTerm, Player

class PlaceholderPuckPediaProvider:
    """Replace this provider when PuckPedia API access is available."""

    def get_contracts(
        self,
        player: Player,
        season_start_year: int,
    ) -> ContractSnapshot:
        return ContractSnapshot(
            ContractTerm(
                season_start_year,
                (player.salary, player.salary, player.salary),
            )
        )

def puckpedia_player_url(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-")
    return f"https://puckpedia.com/player/{slug}"
