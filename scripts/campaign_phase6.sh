#!/bin/sh
# Full Phase 5 campaign on the Phase 6 engine. Runs inside one detached
# container so it survives the editor being closed.
set -u
RUN=20260818Tphase6full02Z
LOG=/work/results/phase5/${RUN}_campaign.log
mkdir -p /work/results/phase5
{
  echo "campaign start $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  python3 -m phase5.run_phase5 \
    --output /work/results/phase5/${RUN} --run-id ${RUN} \
    --config phase5/config_phase6.json
  code=$?
  echo "CAMPAIGN_EXIT=${code}"
  if [ ${code} -eq 0 ]; then
    echo "-- regression suites"
    python3 -m unittest benchmark.test_harness phase2.test_phase2 \
      phase3.test_phase3 phase4a.test_phase4a phase4b.test_phase4b \
      phase5.test_phase5 phase6.test_atleast_boxes
    echo "SUITES_EXIT=$?"
  else
    echo "suites skipped: campaign did not finish cleanly"
  fi
  echo "campaign end $(date -u +%Y-%m-%dT%H:%M:%SZ)"
} 2>&1 | tee "${LOG}"