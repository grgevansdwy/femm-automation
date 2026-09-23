"""Everything about the agent that a person can change without editing code.

- prompt_defs.py - the editable prompts: which exist, what placeholders each
  understands, and their default text (prompts/<key>.txt).
- templating.py - the {{placeholder}} syntax those prompts are written in.
- settings.py - AgentSettings (numbers + prompt overrides), validation, and
  load/save through agent/repositories/agent_settings_repository.py.

Defaults live in code (agent/config.py for numbers, prompts/*.txt for text);
only what a person changed is stored.
"""
