"""Publish contract grids to an Excel workbook through Microsoft Graph."""
import atexit
from pathlib import Path
from typing import Sequence
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import config

PROJECT_ROOT = Path(getattr(config, "PROJECT_ROOT", "") or Path(__file__).parent)

def _http_session() -> requests.Session:
    retry = Retry(
        total=5,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "POST", "PATCH", "DELETE"),
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session

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
    scopes = getattr(
        config,
        "AZURE_SCOPES",
        ["Files.ReadWrite.All", "Sites.ReadWrite.All", "User.Read"],
    )
    app = msal.PublicClientApplication(
        config.AZURE_CLIENT_ID,
        authority=getattr(
            config,
            "AZURE_AUTHORITY",
            "https://login.microsoftonline.com/consumers",
        ),
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
        create_worksheet: bool = False,
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
        if create_worksheet:
            self.sheet_url = self._ensure_worksheet(worksheet)

    def _ensure_worksheet(self, worksheet: str) -> str:
        response = self.session.get(self.sheet_url, timeout=60)
        if response.status_code == 404:
            response = self.session.post(
                f"{self.workbook_url}/worksheets/add",
                json={"name": worksheet},
                timeout=60,
            )
        if not response.ok:
            raise RuntimeError(
                "Microsoft Graph could not get or create worksheet "
                f"{worksheet!r}: {response.status_code} {response.text}"
            )
        worksheet_id = response.json().get("id")
        if not worksheet_id:
            raise RuntimeError(
                f"Microsoft Graph returned no ID for worksheet {worksheet!r}"
            )
        return (
            f"{self.workbook_url}/worksheets/"
            f"{quote(worksheet_id, safe='')}"
        )

    def _request(self, method: str, suffix: str, **kwargs) -> requests.Response:
        response = self.session.request(
            method,
            f"{self.sheet_url}/{suffix}",
            timeout=60,
            **kwargs,
        )
        if not response.ok:
            raise RuntimeError(
                f"Microsoft Graph returned {response.status_code} for {suffix}: "
                f"{response.text}"
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
                "GET",
                f"tables/{table_id}/dataBodyRange",
            ).json()
            existing_rows = body_range.get(
                "rowCount",
                len(body_range.get("values", [])),
            )
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
            self._request(
                "POST",
                f"range(address='{address}')/clear",
                json={"applyTo": "Contents"},
            )
        self._request(
            "PATCH",
            f"range(address='{destination}')",
            json={"formulas": values},
        )

def publish_grid_to_excel(
    headers: Sequence[str],
    rows: Sequence[Sequence[object]],
    summary_headers: Sequence[str],
    summary_rows: Sequence[Sequence[object]],
) -> None:
    required = ("AZURE_DRIVE_ID", "AZURE_WORKBOOK_ITEM_ID", "AZURE_WORKSHEET_NAME")
    missing = [name for name in required if not getattr(config, name, "")]
    if missing:
        raise RuntimeError(f"Azure upload is not configured; set {', '.join(missing)}")
    token = _acquire_azure_token()
    publisher = ExcelGraphPublisher(
        token,
        config.AZURE_DRIVE_ID,
        config.AZURE_WORKBOOK_ITEM_ID,
        config.AZURE_WORKSHEET_NAME,
        getattr(config, "AZURE_TABLE_NAME", ""),
    )
    publisher.replace_grid([list(headers), *[list(row) for row in rows]])
    summary_publisher = ExcelGraphPublisher(
        token,
        config.AZURE_DRIVE_ID,
        config.AZURE_WORKBOOK_ITEM_ID,
        getattr(config, "AZURE_CAP_SUMMARY_WORKSHEET_NAME", "Cap Summary"),
        create_worksheet=True,
    )
    summary_publisher.replace_grid([
        list(summary_headers),
        *[list(row) for row in summary_rows],
    ])
