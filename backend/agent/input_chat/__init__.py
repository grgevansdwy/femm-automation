"""The interview phase: turns a freeform user request into an AgentSpec, the one
structured input the search (agent/femm_agent/) runs against.

- models/ - what an interview produces and the search reads:
    objective.py - Objective: the vocabulary of what can be required/preferred
      (a simulated result, a design variable, a derived quantity), and margin(),
      how far a result sits inside/outside one. femm_agent/services/scorer.py
      reads it just as much as interview.py does - it is the shared language
      both phases speak about what "meets the goal" means.
    spec.py - AgentSpec: the finalized input a conversation produces.
    variables.py - VariableSpec, and which CoilSet fields are free to change:
      built here from the interview, read again by femm_agent/services/optimize.py
      every round to know what the model may propose.
- services/interview.py - the conversation loop itself: InterviewState,
  run_interview_turn(), the finalize_spec tool and its validation.
- tests/ - this phase's tests.

No GUI or LLM-transport code lives here: agent/services/gui_bridge.py runs a turn
on a background thread for the GUI, and agent/services/llm_client.py is the
shared HTTP/streaming plumbing both phases call into. There is no repositories/
layer either - a run's interview and search rows live in the same two tables, so
that store is shared: agent/repositories/ai_run_repository.py.
"""
