import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

# Generic LLM provider selection (anthropic | gemini). Default keeps backward compat.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic").lower()
LLM_ENDPOINT = os.getenv("LLM_ENDPOINT", "")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "gemini-3.1-pro-preview")
BRAVE_API_KEY = os.getenv("BRAVE_SEARCH_API_KEY", "")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")
FMP_API_KEY = os.getenv("FMP_API_KEY", "")
ALPHAVANTAGE_API_KEY = os.getenv("ALPHAVANTAGE_API_KEY", "")
FRED_API_KEY = os.getenv("FRED_API_KEY", "")
SEC_USER_AGENT = os.getenv(
    "SEC_EDGAR_USER_AGENT", "Portfolio Research Agent contact@example.com"
)

OUTPUTS_DIR = ROOT / "outputs"
RUNS_DIR = ROOT / "runs"
OUTPUTS_DIR.mkdir(exist_ok=True)
RUNS_DIR.mkdir(exist_ok=True)

MAX_ITERATIONS = 25
MAX_OUTPUT_TOKENS = 32000
SSE_PREVIEW_CHARS = 600  # UI-only preview length for tool_result cards (full result still sent to LLM)
DEFAULT_TOOL_TIMEOUT = 30.0
DEFAULT_RETRY_ATTEMPTS = 3
HEARTBEAT_SECONDS = 15
