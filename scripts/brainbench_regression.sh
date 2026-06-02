#!/usr/bin/env bash
# brainbench_regression.sh — CI regression gate for BrainBench
#
# Pulls top-N highest-judge captured sessions across all projects from
# dash.dash_brainbench_corpus, POSTs to /api/projects/{slug}/brainbench/runs
# per project, polls until done, fetches summary, exits non-zero iff
#   regressions > 0 AND avg_score_delta < -0.3
#
# Env:
#   DASH_API_URL     base URL (default http://localhost:8000)
#   DASH_API_TOKEN   bearer token (required, super-admin)
#   DASH_DB_URL      psql connection string (required for corpus selection)
#   TOP_N            number of corpus rows (default 10)
#   POLL_TIMEOUT     seconds to wait per run (default 900)
#   FAIL_DELTA       fail threshold for avg_score_delta (default -0.3)
set -euo pipefail

DASH_API_URL="${DASH_API_URL:-http://localhost:8000}"
DASH_API_TOKEN="${DASH_API_TOKEN:?DASH_API_TOKEN required}"
DASH_DB_URL="${DASH_DB_URL:?DASH_DB_URL required (psql conn string)}"
TOP_N="${TOP_N:-10}"
POLL_TIMEOUT="${POLL_TIMEOUT:-900}"
FAIL_DELTA="${FAIL_DELTA:--0.3}"

AUTH="Authorization: Bearer ${DASH_API_TOKEN}"
JSON="Content-Type: application/json"

echo "BrainBench Regression Gate"
echo "  API: ${DASH_API_URL}  TOP_N=${TOP_N}  FAIL_DELTA=${FAIL_DELTA}"
echo

ROWS=$(psql "${DASH_DB_URL}" -At -F'|' -c "
  SELECT project_slug, id
    FROM dash.dash_brainbench_corpus
   WHERE original_judge_score IS NOT NULL
   ORDER BY original_judge_score DESC, created_at DESC
   LIMIT ${TOP_N};
" || true)

if [[ -z "${ROWS}" ]]; then
  echo "Empty corpus — nothing to replay. Exiting OK."
  exit 0
fi

declare -A PROJ_CIDS
while IFS='|' read -r slug cid; do
  [[ -z "${slug}" || -z "${cid}" ]] && continue
  PROJ_CIDS["${slug}"]+="${cid},"
done <<< "${ROWS}"

TOTAL_REG=0; TOTAL_WIN=0; TOTAL_TIE=0; TOTAL_ERR=0
DELTA_SUM=0; DELTA_N=0
FAIL=0
SUMMARY_LINES=()

for slug in "${!PROJ_CIDS[@]}"; do
  cids="${PROJ_CIDS[$slug]%,}"
  ids_json="[$(echo "${cids}" | sed 's/,/, /g')]"
  label="ci_$(date +%s)_${slug}"
  echo "▶ ${slug}: replay corpus_ids=${cids}"
  start=$(curl -fsS -X POST "${DASH_API_URL}/api/projects/${slug}/brainbench/runs" \
    -H "${AUTH}" -H "${JSON}" \
    -d "{\"corpus_ids\": ${ids_json}, \"run_label\": \"${label}\"}")
  run_id=$(echo "${start}" | python3 -c "import sys,json; print(json.load(sys.stdin).get('run_id',0))")
  if [[ "${run_id}" == "0" ]]; then
    echo "  ✗ failed to start run for ${slug}"; FAIL=1; continue
  fi

  waited=0; status="running"
  while [[ ${waited} -lt ${POLL_TIMEOUT} ]]; do
    sleep 5; waited=$((waited+5))
    detail=$(curl -fsS "${DASH_API_URL}/api/projects/${slug}/brainbench/runs/${run_id}" -H "${AUTH}" || echo "{}")
    status=$(echo "${detail}" | python3 -c "import sys,json; print(json.load(sys.stdin).get('run',{}).get('status','running'))" 2>/dev/null || echo "running")
    [[ "${status}" == "done" || "${status}" == "failed" ]] && break
  done

  summary=$(echo "${detail}" | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin).get('run',{}).get('summary',{})))")
  read -r wins regs ties errs total avg <<<"$(echo "${summary}" | python3 -c "
import sys,json
s=json.load(sys.stdin) or {}
print(s.get('wins',0), s.get('regressions',0), s.get('ties',0), s.get('errors',0), s.get('total',0), s.get('avg_score_delta','nan'))")"

  TOTAL_WIN=$((TOTAL_WIN+wins)); TOTAL_REG=$((TOTAL_REG+regs))
  TOTAL_TIE=$((TOTAL_TIE+ties)); TOTAL_ERR=$((TOTAL_ERR+errs))
  if [[ "${avg}" != "nan" && "${avg}" != "None" ]]; then
    DELTA_SUM=$(python3 -c "print(${DELTA_SUM} + ${avg})")
    DELTA_N=$((DELTA_N+1))
  fi
  SUMMARY_LINES+=("$(printf '  %-30s run=%s  W=%s R=%s T=%s E=%s avg_Δ=%s' "${slug}" "${run_id}" "${wins}" "${regs}" "${ties}" "${errs}" "${avg}")")
done

AVG_TOTAL="nan"
[[ ${DELTA_N} -gt 0 ]] && AVG_TOTAL=$(python3 -c "print(round(${DELTA_SUM}/${DELTA_N},3))")

echo
echo "═══ BrainBench Regression Summary ═══"
printf '%s\n' "${SUMMARY_LINES[@]}"
echo "─────────────────────────────────────"
printf '  TOTAL  W=%s R=%s T=%s E=%s  avg_Δ=%s\n' "${TOTAL_WIN}" "${TOTAL_REG}" "${TOTAL_TIE}" "${TOTAL_ERR}" "${AVG_TOTAL}"
echo "═════════════════════════════════════"

if [[ ${TOTAL_REG} -gt 0 && "${AVG_TOTAL}" != "nan" ]]; then
  if python3 -c "import sys; sys.exit(0 if ${AVG_TOTAL} < ${FAIL_DELTA} else 1)"; then
    echo "REGRESSION DETECTED (regressions=${TOTAL_REG}, avg_Δ=${AVG_TOTAL} < ${FAIL_DELTA})"
    exit 2
  fi
fi
[[ ${FAIL} -ne 0 ]] && exit 1
echo "PASS"
exit 0
