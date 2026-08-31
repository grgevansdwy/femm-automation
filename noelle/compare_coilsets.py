# =============================================================================
# compare_coilsets.py                                            Noel Hosking
#
# Thin CLI entry point. The comparison itself lives in run_comparison.py so it
# can be called from other code (e.g. a web UI) as well as from the shell -
# see that file's module banner for what the simulation actually does.
# =============================================================================
from repositories import coilset_repository as cc
from run_comparison import run_comparison, DEFAULT_RC_TEST_DISTANCES

if __name__ == '__main__':
    run, results = run_comparison([cc.TR302_and_225_000056], DEFAULT_RC_TEST_DISTANCES)
    print(f"Results written to {results.dir}")
