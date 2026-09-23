"""The FEMM search agent: takes a finalized AgentSpec (agent/input_chat/) and runs
the evaluator-optimizer loop that proposes CoilSet candidates and simulates them
in FEMM until the goal is met, the round budget runs out, or the model stops.

- models/ - schema.py: the propose_coilset tool schema, and validating what a
  candidate delta actually contains. state.py: AgentRunState / RoundState /
  CandidateResult, the live, pollable state a round mutates in place - the
  Job-equivalent for a search.
- services/ -
    optimize.py - the loop body: propose -> evaluate -> feed back -> repeat.
    scorer.py - pure scoring: a candidate vs. baseline, per objective.
    prompts.py - the search phase's system prompt.
    outcome.py - the end-of-run report: why a search didn't meet its goal.
    followup.py - the chat once a search has started: answers questions about a
      running or finished run, but (structurally, via no tools) can never change it.
- tests/ - this phase's tests.

No GUI or LLM-transport code lives here: agent/femm_agent/services/runner.py runs
optimize_loop() on a background thread for the GUI, and agent/services/llm_client.py
is the shared HTTP/streaming plumbing every request goes through, interview phase
included. Storage is shared with the interview phase: agent/repositories/.
"""
