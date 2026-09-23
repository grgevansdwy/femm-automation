"""Plumbing shared by both phases - and the whole surface frontend/ calls into.

- llm_client.py - the HTTP/streaming client every LLM request goes through,
  interview and search alike.
- gui_bridge.py - InterviewRunner: runs an interview (and follow-up) turn on a
  background thread so a GUI can poll it every frame.
- runner.py - AgentRunner: runs the search loop on a background thread the same way.

Behavior that belongs to one phase lives with that phase instead:
input_chat/services/ (the interview) and femm_agent/services/ (the search).
"""
