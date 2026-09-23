"""Plumbing shared by both phases.

- llm_client.py - the HTTP/streaming client every LLM request goes through,
  interview and search alike.

Everything that belongs to one phase lives with that phase instead, including
the background-thread runners frontend/ calls into:
input_chat/services/runner.py (InterviewRunner: an interview or follow-up turn) and
femm_agent/services/runner.py (AgentRunner: the search loop).
"""
