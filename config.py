import os
from dotenv import load_dotenv

load_dotenv()

MASTER_DB_URL = os.getenv("MASTER_DB_URL")
MASTER_DB_SERVICE_KEY = os.getenv("MASTER_DB_SERVICE_KEY")
PROXY_URL = os.getenv("PROXY_URL")
COLLECTOR_API_KEY = os.getenv("COLLECTOR_API_KEY")
PORT = int(os.getenv("PORT", "8000"))
