"""AgentSettings: every number and prompt a person can change without editing
code, with its default, its validation, and load/save.

Defaults come from agent/config.py (numbers) and settings/prompts/*.txt (text);
only what differs from them is stored (agent/repositories/agent_settings_repository.py).
A run copies the settings in force when it starts into AgentSpec.settings, so
editing them mid-run never changes that run.
"""

import dataclasses

from agent import config
from agent.repositories import agent_settings_repository as repository
from agent.settings.prompt_defs import PROMPT_DEFS
from agent.settings.templating import check_placeholders

# field -> (label, unit) for the Settings screen and for error messages.
FIELD_LABELS = {
    "api_base_url": "LLM server URL", "model": "Model name",
    "llm_time_limit_s": "Time limit per AI request (s)", "llm_max_tokens": "Max tokens per AI reply",
    "temperature": "Sampling temperature", "top_p": "Sampling top-p", "seed": "Sampling seed",
    "max_tool_calls": "Max rounds per search", "candidates_per_round": "Candidates per round",
    "keep_best_candidates": "Best candidates remembered", "round_llm_attempts": "Tries per round's AI request",
    "search_attempts": "Failed rounds before a search gives up", "max_distances": "Max distances per search",
    "search_time_limit_s": "Time limit per search (s)", "max_simulations": "Max simulations per search",
    "plateau_rounds": "Rounds without improvement after a pass", "stall_rounds": "Rounds without improvement before a pass",
    "noise_points": "Smallest real improvement (points)",
}

# One line of help per field, shown under it in the Settings screen.
FIELD_HELP = {
    "api_base_url": "Any OpenAI-compatible server (the app adds /v1/chat/completions). Also settable with the LLM_API_BASE environment variable, which only changes this default.",
    "model": "Must support tool calling. Also settable with AGENT_MODEL (changes this default).",
    "llm_time_limit_s": "Hard wall-clock limit for one request, including waiting in the server's queue.",
    "llm_max_tokens": "Cap on one reply's length, thinking included. A runaway reply that hits it counts as a failed try.",
    "temperature": "Sent with every AI request. Higher = more varied replies; 0 = always the likeliest token.",
    "top_p": "Sent with every AI request. Only the most likely tokens adding up to this probability are considered.",
    "seed": "Sent with every AI request. -1 = a new random seed each time, so repeated searches differ.",
    "max_tool_calls": "How many rounds (AI replies) a search may use. Counted across resumes; a text-only reply uses one.",
    "search_time_limit_s": "Wall-clock limit for a whole search. No AI request or simulation starts after it.",
    "max_simulations": "Fresh simulations a search may run (one design at one distance = 1). Cached results and the baseline are free.",
    "plateau_rounds": "Once a design meets the goal, the search stops after this many rounds in a row without a better one.",
    "stall_rounds": "Before any design meets the goal: after this many rounds without getting closer the AI is told it is stalled; 2 rounds later the search stops.",
    "noise_points": "A round counts as an improvement only if it is better by more than this many percentage points (about the solver's own noise).",
    "candidates_per_round": "Most designs the AI may propose in one reply; they are simulated at the same time. 1 = one at a time.",
    "keep_best_candidates": "How many of the best designs so far are re-sent to the AI each round, one line each.",
    "round_llm_attempts": "How many times a round's AI request is tried before that round counts as failed.",
    "search_attempts": "A round whose AI request fails every try is a failed round; the search resumes with its memory kept until this many have failed.",
    "max_distances": "Most distances one search may simulate. Each is a full extra FEMM solve per candidate.",
}


@dataclasses.dataclass
class AgentSettings:
    # --- LLM connection
    api_base_url: str = config.API_BASE_URL
    model: str = config.MODEL
    llm_time_limit_s: float = config.LLM_TIME_LIMIT_S
    llm_max_tokens: int = config.LLM_MAX_TOKENS
    temperature: float = config.TEMPERATURE
    top_p: float = config.TOP_P
    seed: int = config.SEED
    # --- search budget
    max_tool_calls: int = config.MAX_TOOL_CALLS
    search_time_limit_s: float = config.SEARCH_TIME_LIMIT_S
    max_simulations: int = config.MAX_SIMULATIONS
    plateau_rounds: int = config.PLATEAU_ROUNDS
    stall_rounds: int = config.STALL_ROUNDS
    noise_points: float = config.NOISE_POINTS
    candidates_per_round: int = config.CANDIDATES_PER_ROUND
    keep_best_candidates: int = config.KEEP_BEST_CANDIDATES
    round_llm_attempts: int = config.ROUND_LLM_ATTEMPTS
    search_attempts: int = config.SEARCH_ATTEMPTS
    max_distances: int = config.MAX_DISTANCES
    # --- prompts: only the ones a person edited, key -> text (see prompt_defs.PROMPT_DEFS)
    prompts: dict = dataclasses.field(default_factory=dict)

    def prompt(self, key: str) -> str:
        """The text in force for a prompt: the edit if there is one, else the default."""
        return self.prompts.get(key) or PROMPT_DEFS[key].default

    def validate(self) -> list[str]:
        """Everything wrong with these settings, as messages a person can act
        on (empty list = OK)."""
        problems = []

        def need(ok, field, rule):
            if not ok:
                problems.append(f"{FIELD_LABELS[field]}: {rule}")

        def number(v):
            return isinstance(v, (int, float)) and not isinstance(v, bool)

        need(isinstance(self.api_base_url, str) and self.api_base_url.strip().startswith(("http://", "https://")),
             "api_base_url", "must start with http:// or https://")
        need(isinstance(self.model, str) and self.model.strip(), "model", "can't be empty")
        need(number(self.llm_time_limit_s) and self.llm_time_limit_s >= 1, "llm_time_limit_s", "must be at least 1")
        need(isinstance(self.llm_max_tokens, int) and self.llm_max_tokens >= 1, "llm_max_tokens", "must be a whole number, at least 1")
        need(number(self.temperature) and 0 <= self.temperature <= 2, "temperature", "must be between 0 and 2")
        need(number(self.top_p) and 0 < self.top_p <= 1, "top_p", "must be above 0 and at most 1")
        need(isinstance(self.seed, int) and not isinstance(self.seed, bool) and self.seed >= -1,
             "seed", "must be a whole number, -1 (random) or higher")
        need(number(self.search_time_limit_s) and self.search_time_limit_s >= 60, "search_time_limit_s", "must be at least 60")
        need(number(self.noise_points) and self.noise_points >= 0, "noise_points", "can't be negative")
        for name in ("max_tool_calls", "candidates_per_round", "keep_best_candidates", "round_llm_attempts",
                     "search_attempts", "max_distances", "max_simulations", "plateau_rounds", "stall_rounds"):
            v = getattr(self, name)
            need(isinstance(v, int) and not isinstance(v, bool) and v >= 1, name, "must be a whole number, at least 1")

        for key, text in self.prompts.items():
            definition = PROMPT_DEFS.get(key)
            if definition is None:
                problems.append(f"Unknown prompt '{key}'")
            elif not isinstance(text, str) or not text.strip():
                problems.append(f"{definition.title}: can't be empty (use Reset to go back to the default)")
            else:
                problems += [f"{definition.title}: {p}" for p in
                             check_placeholders(text, definition.placeholders, definition.required)]
        return problems


def _defaults() -> AgentSettings:
    return AgentSettings()


def _usable_prompt(key: str, text) -> bool:
    """False for a saved prompt this version of the code can no longer fill in
    (a prompt that was since removed or merged into another, or text using
    placeholders that no longer exist) - the default is used instead, rather
    than sending the AI a prompt with raw {{tags}} in it."""
    definition = PROMPT_DEFS.get(key)
    return (definition is not None and isinstance(text, str) and bool(text.strip())
            and not check_placeholders(text, definition.placeholders, definition.required))


def load_settings() -> AgentSettings:
    """Defaults with whatever a person has saved applied on top. A saved value
    that no longer fits this version of the code (unknown key, wrong shape,
    a prompt written for placeholders that have since changed) is ignored
    rather than crashing every run."""
    settings = _defaults()
    known = {f.name for f in dataclasses.fields(AgentSettings)}
    for key, value in repository.load_overrides().items():
        if key.startswith("prompt:"):
            if _usable_prompt(key[7:], value):
                settings.prompts[key[7:]] = value
        elif key in known and key != "prompts":
            setattr(settings, key, value)
    return settings


def save_settings(settings: AgentSettings) -> None:
    """Stores exactly what differs from the defaults. Raises ValueError with
    every problem if the settings don't validate - nothing is saved then."""
    problems = settings.validate()
    if problems:
        raise ValueError("\n".join(problems))
    defaults = dataclasses.asdict(_defaults())
    overrides = {name: value for name, value in dataclasses.asdict(settings).items()
                 if name != "prompts" and value != defaults[name]}
    overrides.update({f"prompt:{key}": text for key, text in settings.prompts.items()
                      if text != PROMPT_DEFS[key].default})
    repository.replace_overrides(overrides)


def settings_from_dict(data: dict | None) -> AgentSettings:
    """Rebuilds settings from a run's stored snapshot (dataclasses.asdict output);
    unknown keys are ignored, missing ones fall back to the defaults."""
    settings = _defaults()
    for f in dataclasses.fields(AgentSettings):
        if data and f.name in data:
            setattr(settings, f.name, data[f.name])
    settings.prompts = {k: v for k, v in settings.prompts.items() if _usable_prompt(k, v)}
    return settings
