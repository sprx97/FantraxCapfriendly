# Project root for full filepaths
PROJECT_ROOT = ""

# This was found by using browser developer mode to sniff the GET request after clicking the "download all players" button,
# and right clicking -> Copy as CURL (bash). The FX_RM seems to be the only relevant cookie, and it doesn't expire until March 24th, 2032.
# If it changes, it also can be found in your browser's cookies. I believe this is unique to my account/login, so I'm going to put it in a
# config file just in case. It seems like it refreshes every time I log in, so if I ever change my password or log out/in, I'll need to update this.
FANTRAX_LOGIN_COOKIE = ""

# This is the client ID of the AAD project that allows me to access onedrive as a user.
# This is needed for being able to access the Excel programatically. Honestly I forget
# how I set this up, so it may be a bit complicated.
AZURE_CLIENT_ID = ""

# This is the email address used to log into AAD (and onedrive), related ot the client ID above
AZURE_USER = ""

# Destination Excel workbook. Used only with main.py --upload.
AZURE_DRIVE_ID = ""
AZURE_WORKBOOK_ITEM_ID = ""
AZURE_WORKSHEET_NAME = ""
# Optional when the worksheet contains exactly one table.
AZURE_TABLE_NAME = ""
AZURE_AUTHORITY = "https://login.microsoftonline.com/consumers"
AZURE_SCOPES = ["Files.ReadWrite.All", "Sites.ReadWrite.All", "User.Read"]
AZURE_TOKEN_CACHE = "/home/jeremy/mdh-hockey/mdhhockey/response_cache/cache.bin"
