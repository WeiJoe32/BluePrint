"""
Central configuration: loads environment variables and defines constants.

Locally, values come from a .env file (see .env.example) via python-dotenv.
On Render, the same variables are set in the dashboard's Environment tab.
"""

import os
from dotenv import load_dotenv

# Load .env when running locally. On Render there is no .env file and this is a no-op.
load_dotenv()

# --- Secrets / auth (set these as environment variables) ---
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")   # Claude API key (backend only)
APP_PIN = os.environ.get("APP_PIN", "")                       # shared login PIN
FLASK_SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "dev-only-change-me")

# --- Server settings ---
PORT = int(os.environ.get("PORT", "5000"))                   # Render provides PORT at runtime
FLASK_DEBUG = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true", "yes")

# The Secure flag on the session cookie only works over HTTPS. Local dev is
# plain http://127.0.0.1, so a Secure cookie would be dropped by the browser
# (silent login failure). Keep this OFF locally; set SESSION_COOKIE_SECURE=true
# on Render, which serves over HTTPS.
SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "").lower() in ("1", "true", "yes")

# When STUB_MODE is on, /api/generate returns a canned SVG with NO API call.
# This lets the frontend be tested locally for free, without a Claude API key.
STUB_MODE = os.environ.get("BLUEPRINT_STUB", "").lower() in ("1", "true", "yes")

# --- Model / limits constants ---
MODEL_DRAFT = "claude-haiku-4-5"  # cheapest test drawings (no adaptive thinking)
MODEL_FAST = "claude-sonnet-5"    # good quality, moderate cost — the default
MODEL_BEST = "claude-opus-4-8"    # highest-quality drawings
MAX_IMAGES = 4                    # most photos accepted per request
MARKER_SIZE_MM = 50               # printed ArUco scale marker edge length (mm)
MARKER_DICT = "DICT_5X5_50"       # OpenCV ArUco dictionary the marker is drawn from
MAX_REFINE_TURNS = 8              # most refinement rounds kept in one conversation
MAX_DIMENSIONS = 6                # most known dimensions accepted per request


def missing_required():
    """Return the names of required env vars that are not set (for a startup warning).

    The API key is only required when STUB_MODE is off, because stub mode never
    calls Claude.
    """
    required = {"APP_PIN": APP_PIN}
    if not STUB_MODE:
        required["ANTHROPIC_API_KEY"] = ANTHROPIC_API_KEY
    return [name for name, value in required.items() if not value]
