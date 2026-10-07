"""The DEFAULT value of every tunable number for the agent - zero dependencies.

These are only defaults: agent/settings/settings.py's AgentSettings starts from
them, and a value someone changes in the GUI's Settings screen is stored in the
database and wins. Code reads the settings, not this module (the environment
variables below just change the defaults).
"""

import os

# Base URL of any OpenAI-compatible /v1/chat/completions server. Defaults to
# the office GPU server - verified working with a real live tool-calling
# round trip.
API_BASE_URL = os.environ.get("LLM_API_BASE", "http://192.168.1.74:8854")
MODEL = os.environ.get("AGENT_MODEL", "Qwen3.8-Flash-Next-UD-Q4_K_XL")  # must support tool calling
MAX_TOOL_CALLS = 50  # hard budget on how many rounds (LLM replies) we'll ask for
# Most candidates the model may propose in one reply, all solved concurrently.
# The prompt asks it to use all of them unless fewer would genuinely be more
# useful. The LLM call dominates a round (~77 s median vs ~8 s of FEMM per
# candidate, from real runs), so several candidates per call is where the time
# win is - see docs/Agent.md §7. 1 restores one-candidate-per-turn.
CANDIDATES_PER_ROUND = 5
# The search's memory (agent/femm_agent/services/optimize.py's search_memory()): each request
# carries the latest round's full results, this many best candidates so far
# (one line each), and a shorter line for every other candidate tried.
KEEP_BEST_CANDIDATES = 10
MAX_DISTANCES = 5  # each one is a full extra FEMM solve per candidate - a hard cap on search cost

# Limits on every AI request (agent/services/llm_client.py). The time limit is wall
# clock for the whole request - queueing, reading the prompt and generating -
# enforced even when the server sends nothing. Raised from 600s to 900s after
# real rounds on a busy shared server queued/generated for 500-600s+ on their
# own; at the office server's ~15-25 tokens/s, 900 s is ~13-22k tokens, so the
# time limit normally binds first - the token cap is the backstop for a fast,
# idle server.
LLM_TIME_LIMIT_S = float(os.environ.get("AGENT_LLM_TIME_LIMIT_S", "900"))
LLM_MAX_TOKENS = int(os.environ.get("AGENT_LLM_MAX_TOKENS", "32000"))
# Sampling, sent explicitly with every request so a run doesn't change when the
# server's own defaults do. These are the office server's defaults as read from
# its /props on 2026-10-07 (it also applies top_k 20 and min_p 0.05, left to it).
# SEED -1 = a fresh random seed per request, so repeated searches differ.
TEMPERATURE = 1.0
TOP_P = 0.95
SEED = -1
# Retry policy for a search (agent/femm_agent/services/runner.py, agent/femm_agent/services/optimize.py): a round's AI
# request is tried this many times; if every try fails, the whole search
# starts over from zero memory, up to SEARCH_ATTEMPTS times in total.
ROUND_LLM_ATTEMPTS = 2
SEARCH_ATTEMPTS = 2


# These configs are just hardcoded for testing when using CLI instead of GUI
BASELINE_CONFIG_ID = "Gen2_coil"  # what every candidate is compared against
DISTANCES = [3.0, 5.0, 8.0, 12.0]  # mm, RC separation sweep

M_TOLERANCE_PCT = 5.0  # candidate's M must stay within this % of baseline, at every distance
MIN_ESR_IMPROVEMENT_PCT = 10.0  # each coil's ESR must drop by at least this % to count as "goal met"

# Which mutual inductance is "the" link: M12=tank<->loop, M13=loop<->RC, M23=tank<->RC.
PRIMARY_M = "M23"
