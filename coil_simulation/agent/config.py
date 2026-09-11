"""Every tunable constant for the agent prototype - zero dependencies,
everything else in this package imports from here."""

import os

# Base URL of any OpenAI-compatible /v1/chat/completions server. Defaults to
# the office GPU server - verified working with a real live tool-calling
# round trip.
API_BASE_URL = os.environ.get("LLM_API_BASE", "http://192.168.1.74:8854")
MODEL = os.environ.get("AGENT_MODEL", "Qwen3.8-Flash-Next-UD-Q4_K_XL")  # must support tool calling
MAX_TOOL_CALLS = 50  # hard budget on how many proposals we'll ask for
KEEP_RECENT_ROUNDS = 10  # rounds kept in full detail; older ones collapse into one summary line each

BASELINE_CONFIG_ID = "Gen2_coil"  # what every candidate is compared against
DISTANCES = [3.0, 5.0, 8.0, 12.0]  # mm, RC separation sweep
MAX_DISTANCES = 5  # each one is a full extra FEMM solve per candidate - a hard cap on search cost

M_TOLERANCE_PCT = 5.0  # candidate's M must stay within this % of baseline, at every distance
MIN_ESR_IMPROVEMENT_PCT = 10.0  # each coil's ESR must drop by at least this % to count as "goal met"

# Which mutual inductance is "the" link: M12=tank<->loop, M13=loop<->RC, M23=tank<->RC.
PRIMARY_M = "M23"
