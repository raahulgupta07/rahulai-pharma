#!/usr/bin/env bash
set -euo pipefail
# =============================================================================
# Dash Full Stress Test — Real-world simulation
# =============================================================================

BASE="http://localhost:8001"
NUM_USERS=10
PROJECTS_PER_USER=3
PASS="test1234"
RUN_ID="r$(date +%s)"  # Unique per run to avoid conflicts
D="/tmp/dash_stress_$$"
rm -rf "$D" && mkdir -p "$D"

TOTAL=0; PASSED=0; FAILED=0

check() {
  TOTAL=$((TOTAL+1))
  local code=$(cat "$2.code" 2>/dev/null || echo "000")
  if echo "$code" | grep -qE "^(${3:-200})$"; then
    PASSED=$((PASSED+1))
  else
    FAILED=$((FAILED+1))
    echo "  ✗ $1 — HTTP $code: $(head -c 120 "$2.body" 2>/dev/null)"
  fi
}

echo "╔══════════════════════════════════════════════════════════╗"
echo "║  DASH STRESS TEST — $NUM_USERS users × $PROJECTS_PER_USER projects (run=$RUN_ID)  ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# === PHASE 1: Register ===
echo "━━━ PHASE 1: Register $NUM_USERS users ━━━"
for i in $(seq 1 $NUM_USERS); do
  (curl -s -o "$D/reg$i.body" -w "%{http_code}" -X POST \
    -H "Content-Type: application/json" \
    -d "{\"username\":\"${RUN_ID}_u${i}\",\"password\":\"$PASS\",\"email\":\"${RUN_ID}_u${i}@t.com\"}" \
    "$BASE/api/auth/register" > "$D/reg$i.code") &
done; wait
for i in $(seq 1 $NUM_USERS); do check "register $i" "$D/reg$i"; done; echo ""

# === PHASE 2: Login ===
echo "━━━ PHASE 2: Login $NUM_USERS users ━━━"
for i in $(seq 1 $NUM_USERS); do
  (curl -s -o "$D/tok$i.body" -w "%{http_code}" -X POST \
    -H "Content-Type: application/json" \
    -d "{\"username\":\"${RUN_ID}_u${i}\",\"password\":\"$PASS\"}" \
    "$BASE/api/auth/login" > "$D/tok$i.code") &
done; wait
# Extract tokens into files
for i in $(seq 1 $NUM_USERS); do
  check "login $i" "$D/tok$i"
  python3 -c "import json; print(json.load(open('$D/tok$i.body'))['token'])" > "$D/t$i" 2>/dev/null || echo "" > "$D/t$i"
done; echo ""

tok() { cat "$D/t$1" 2>/dev/null; }

# === PHASE 3: Create projects ===
echo "━━━ PHASE 3: Create $((NUM_USERS*PROJECTS_PER_USER)) projects ━━━"
for i in $(seq 1 $NUM_USERS); do
  for p in $(seq 1 $PROJECTS_PER_USER); do
    (curl -s -o "$D/p${i}_${p}.body" -w "%{http_code}" -X POST \
      -H "Authorization: Bearer $(tok $i)" \
      "$BASE/api/projects?name=${RUN_ID}_P${i}_${p}&agent_name=Agent${p}" > "$D/p${i}_${p}.code") &
  done
done; wait
for i in $(seq 1 $NUM_USERS); do for p in $(seq 1 $PROJECTS_PER_USER); do
  check "proj u${i}/p${p}" "$D/p${i}_${p}"
done; done; echo ""

slug() { python3 -c "import json; print(json.load(open('$D/p${1}_${2}.body')).get('slug',''))" 2>/dev/null; }

# === PHASE 4: List projects ===
echo "━━━ PHASE 4: List projects ($NUM_USERS concurrent) ━━━"
for i in $(seq 1 $NUM_USERS); do
  (curl -s -o "$D/ls$i.body" -w "%{http_code}" \
    -H "Authorization: Bearer $(tok $i)" "$BASE/api/projects" > "$D/ls$i.code") &
done; wait
for i in $(seq 1 $NUM_USERS); do check "list u$i" "$D/ls$i"; done; echo ""

# === PHASE 5: Upload CSV ===
echo "━━━ PHASE 5: Upload CSV to $((NUM_USERS*PROJECTS_PER_USER)) projects ━━━"
CSV="$D/data.csv"
echo "id,product,revenue,region,month" > "$CSV"
for r in $(seq 1 50); do echo "$r,Prod_$r,$((RANDOM%9000+1000)),$(echo North South East West | tr ' ' '\n' | shuf | head -1),2025-$(printf '%02d' $((r%12+1)))-15" >> "$CSV"; done

for i in $(seq 1 $NUM_USERS); do for p in $(seq 1 $PROJECTS_PER_USER); do
  S=$(slug $i $p)
  [ -z "$S" ] && continue
  (curl -s -o "$D/up${i}_${p}.body" -w "%{http_code}" -X POST \
    -H "Authorization: Bearer $(tok $i)" \
    -F "file=@$CSV" -F "project=$S" \
    "$BASE/api/upload" > "$D/up${i}_${p}.code") &
done; done; wait
for i in $(seq 1 $NUM_USERS); do for p in $(seq 1 $PROJECTS_PER_USER); do
  [ -f "$D/up${i}_${p}.code" ] && check "upload u${i}/p${p}" "$D/up${i}_${p}"
done; done; echo ""

# === PHASE 6: Upload docs ===
echo "━━━ PHASE 6: Upload docs to $NUM_USERS projects ━━━"
DOC="$D/report.txt"
echo -e "Q1 Sales Report\nRevenue: 450K\nChurn: 2.1%\nTop region: North" > "$DOC"
for i in $(seq 1 $NUM_USERS); do
  S=$(slug $i 1)
  [ -z "$S" ] && continue
  (curl -s -o "$D/doc$i.body" -w "%{http_code}" -X POST \
    -H "Authorization: Bearer $(tok $i)" \
    -F "file=@$DOC;filename=report.txt" -F "project=$S" \
    "$BASE/api/upload-doc" > "$D/doc$i.code") &
done; wait
for i in $(seq 1 $NUM_USERS); do [ -f "$D/doc$i.code" ] && check "doc u$i" "$D/doc$i"; done; echo ""

# === PHASE 7: Chat (most intensive) ===
echo "━━━ PHASE 7: Chat — $NUM_USERS users simultaneously ━━━"
QS=("What is the total revenue?" "Revenue by region" "Top product by revenue" "Show monthly trends" "Data overview")
for i in $(seq 1 $NUM_USERS); do
  S=$(slug $i 1); [ -z "$S" ] && continue
  Q="${QS[$(((i-1)%5))]}"
  (curl -s -o "$D/ch$i.body" -w "%{http_code}" --max-time 120 -X POST \
    -H "Authorization: Bearer $(tok $i)" \
    -F "message=$Q" -F "stream=false" -F "reasoning=fast" \
    "$BASE/api/projects/$S/chat" > "$D/ch$i.code") &
done; wait
for i in $(seq 1 $NUM_USERS); do [ -f "$D/ch$i.code" ] && check "chat u$i" "$D/ch$i"; done; echo ""

# === PHASE 8: Learning (memories + feedback + reads) ===
echo "━━━ PHASE 8: Learning — 50 concurrent requests ━━━"
for i in $(seq 1 $NUM_USERS); do
  S=$(slug $i 1); [ -z "$S" ] && continue; T=$(tok $i)
  # Write: memory
  (curl -s -o "$D/mem$i.body" -w "%{http_code}" -X POST \
    -H "Content-Type: application/json" -H "Authorization: Bearer $T" \
    -d "{\"fact\":\"North is top region for user $i\"}" \
    "$BASE/api/projects/$S/memories" > "$D/mem$i.code") &
  # Write: feedback
  (curl -s -o "$D/fb$i.body" -w "%{http_code}" -X POST \
    -H "Content-Type: application/json" -H "Authorization: Bearer $T" \
    -d "{\"question\":\"Total revenue?\",\"answer\":\"500K\",\"rating\":\"up\"}" \
    "$BASE/api/projects/$S/feedback" > "$D/fb$i.code") &
  # Read: memories
  (curl -s -o "$D/lm$i.body" -w "%{http_code}" -H "Authorization: Bearer $T" \
    "$BASE/api/projects/$S/memories" > "$D/lm$i.code") &
  # Read: patterns
  (curl -s -o "$D/qp$i.body" -w "%{http_code}" -H "Authorization: Bearer $T" \
    "$BASE/api/projects/$S/query-patterns" > "$D/qp$i.code") &
  # Read: evals
  (curl -s -o "$D/ev$i.body" -w "%{http_code}" -H "Authorization: Bearer $T" \
    "$BASE/api/projects/$S/evals" > "$D/ev$i.code") &
done; wait
for i in $(seq 1 $NUM_USERS); do
  [ -f "$D/mem$i.code" ] && check "memory w u$i" "$D/mem$i"
  [ -f "$D/fb$i.code" ] && check "feedback w u$i" "$D/fb$i"
  [ -f "$D/lm$i.code" ] && check "memory r u$i" "$D/lm$i"
  [ -f "$D/qp$i.code" ] && check "patterns u$i" "$D/qp$i"
  [ -f "$D/ev$i.code" ] && check "evals u$i" "$D/ev$i"
done; echo ""

# === PHASE 9: Dashboards ===
echo "━━━ PHASE 9: Dashboards — create + list ━━━"
for i in $(seq 1 $NUM_USERS); do
  S=$(slug $i 1); [ -z "$S" ] && continue; T=$(tok $i)
  (curl -s -o "$D/dc$i.body" -w "%{http_code}" -X POST \
    -H "Content-Type: application/json" -H "Authorization: Bearer $T" \
    -d "{\"name\":\"Dash_${RUN_ID}_$i\",\"widgets\":[]}" \
    "$BASE/api/projects/$S/dashboards" > "$D/dc$i.code") &
  (curl -s -o "$D/dl$i.body" -w "%{http_code}" -H "Authorization: Bearer $T" \
    "$BASE/api/projects/$S/dashboards" > "$D/dl$i.code") &
done; wait
for i in $(seq 1 $NUM_USERS); do
  [ -f "$D/dc$i.code" ] && check "dash create u$i" "$D/dc$i"
  [ -f "$D/dl$i.code" ] && check "dash list u$i" "$D/dl$i"
done; echo ""

# === PHASE 10: Rules + Scores ===
echo "━━━ PHASE 10: Rules + Scores ━━━"
for i in $(seq 1 $NUM_USERS); do
  S=$(slug $i 1); [ -z "$S" ] && continue; T=$(tok $i)
  (curl -s -o "$D/rl$i.body" -w "%{http_code}" -H "Authorization: Bearer $T" \
    "$BASE/api/projects/$S/rules" > "$D/rl$i.code") &
  (curl -s -o "$D/sc$i.body" -w "%{http_code}" -H "Authorization: Bearer $T" \
    "$BASE/api/projects/$S/scores" > "$D/sc$i.code") &
done; wait
for i in $(seq 1 $NUM_USERS); do
  [ -f "$D/rl$i.code" ] && check "rules u$i" "$D/rl$i"
  [ -f "$D/sc$i.code" ] && check "scores u$i" "$D/sc$i"
done; echo ""

# === PHASE 11: Self-learning endpoints ===
echo "━━━ PHASE 11: Self-learning — 90 concurrent reads ━━━"
for i in $(seq 1 $NUM_USERS); do
  S=$(slug $i 1); [ -z "$S" ] && continue; T=$(tok $i)
  for ep in insights preferences meta-learnings evolved-instructions training-runs drift-alerts relationships agents workflows-db; do
    (curl -s -o "$D/${ep}_$i.body" -w "%{http_code}" -H "Authorization: Bearer $T" \
      "$BASE/api/projects/$S/$ep" > "$D/${ep}_$i.code") &
  done
done; wait
for i in $(seq 1 $NUM_USERS); do
  for ep in insights preferences meta-learnings evolved-instructions training-runs drift-alerts relationships agents workflows-db; do
    [ -f "$D/${ep}_$i.code" ] && check "$ep u$i" "$D/${ep}_$i"
  done
done; echo ""

# === PHASE 12: Tables + Knowledge + Sessions ===
echo "━━━ PHASE 12: Tables + Knowledge + Sessions ━━━"
for i in $(seq 1 $NUM_USERS); do
  S=$(slug $i 1); [ -z "$S" ] && continue; T=$(tok $i)
  (curl -s -o "$D/tb$i.body" -w "%{http_code}" -H "Authorization: Bearer $T" \
    "$BASE/api/tables?project=$S" > "$D/tb$i.code") &
  (curl -s -o "$D/kf$i.body" -w "%{http_code}" -H "Authorization: Bearer $T" \
    "$BASE/api/knowledge-files?project=$S" > "$D/kf$i.code") &
  (curl -s -o "$D/ln$i.body" -w "%{http_code}" -H "Authorization: Bearer $T" \
    "$BASE/api/lineage?project=$S" > "$D/ln$i.code") &
  (curl -s -o "$D/se$i.body" -w "%{http_code}" -H "Authorization: Bearer $T" \
    "$BASE/api/sessions?project=$S" > "$D/se$i.code") &
  (curl -s -o "$D/do$i.body" -w "%{http_code}" -H "Authorization: Bearer $T" \
    "$BASE/api/docs?project=$S" > "$D/do$i.code") &
done; wait
for i in $(seq 1 $NUM_USERS); do
  [ -f "$D/tb$i.code" ] && check "tables u$i" "$D/tb$i"
  [ -f "$D/kf$i.code" ] && check "knowledge u$i" "$D/kf$i"
  [ -f "$D/ln$i.code" ] && check "lineage u$i" "$D/ln$i"
  [ -f "$D/se$i.code" ] && check "sessions u$i" "$D/se$i"
  [ -f "$D/do$i.code" ] && check "docs u$i" "$D/do$i"
done; echo ""

# === PHASE 13: Schedules ===
echo "━━━ PHASE 13: Schedules ━━━"
for i in $(seq 1 $NUM_USERS); do
  S=$(slug $i 1); [ -z "$S" ] && continue; T=$(tok $i)
  (curl -s -o "$D/sc_c$i.body" -w "%{http_code}" -X POST -H "Authorization: Bearer $T" \
    "$BASE/api/projects/$S/schedules?name=Daily_${RUN_ID}&prompt=Total+revenue&cron=daily" > "$D/sc_c$i.code") &
  (curl -s -o "$D/sc_l$i.body" -w "%{http_code}" -H "Authorization: Bearer $T" \
    "$BASE/api/projects/$S/schedules" > "$D/sc_l$i.code") &
done; wait
for i in $(seq 1 $NUM_USERS); do
  [ -f "$D/sc_c$i.code" ] && check "sched create u$i" "$D/sc_c$i"
  [ -f "$D/sc_l$i.code" ] && check "sched list u$i" "$D/sc_l$i"
done; echo ""

# === PHASE 14: PEAK LOAD — everything at once ===
echo "━━━ PHASE 14: PEAK LOAD — all operations at once ━━━"
PK_PASS=0; PK_FAIL=0; PK_TOT=0
for i in $(seq 1 $NUM_USERS); do
  S=$(slug $i 1); [ -z "$S" ] && continue; T=$(tok $i)
  curl -s -o /dev/null -w "%{http_code}" "$BASE/health" > "$D/pk_${i}_1" &
  curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $T" "$BASE/api/projects" > "$D/pk_${i}_2" &
  curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $T" "$BASE/api/auth/check" > "$D/pk_${i}_3" &
  curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $T" "$BASE/api/projects/$S/memories" > "$D/pk_${i}_4" &
  curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $T" "$BASE/api/projects/$S/feedback" > "$D/pk_${i}_5" &
  curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $T" "$BASE/api/projects/$S/dashboards" > "$D/pk_${i}_6" &
  curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $T" "$BASE/api/projects/$S/insights" > "$D/pk_${i}_7" &
  curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $T" "$BASE/api/projects/$S/workflows-db" > "$D/pk_${i}_8" &
  curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $T" "$BASE/api/projects/$S/agents" > "$D/pk_${i}_9" &
  curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $T" "$BASE/api/projects/$S/evals" > "$D/pk_${i}_10" &
done; wait

for f in "$D"/pk_*; do
  PK_TOT=$((PK_TOT+1))
  [ "$(cat "$f")" = "200" ] && PK_PASS=$((PK_PASS+1)) || PK_FAIL=$((PK_FAIL+1))
done
echo "  Peak: $PK_PASS/$PK_TOT passed ($PK_FAIL failed)"
TOTAL=$((TOTAL+PK_TOT)); PASSED=$((PASSED+PK_PASS)); FAILED=$((FAILED+PK_FAIL))
echo ""

# === DB CHECK ===
echo "━━━ DB CONNECTIONS ━━━"
docker exec dash-db psql -U ai -d ai -c "SELECT count(*) as total, state FROM pg_stat_activity WHERE datname='ai' GROUP BY state ORDER BY total DESC;"

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
printf "║  TOTAL: %-4d  PASS: %-4d  FAIL: %-4d  RATE: %5.1f%%     ║\n" \
  "$TOTAL" "$PASSED" "$FAILED" "$(echo "scale=1; $PASSED*100/$TOTAL" | bc)"
echo "╚══════════════════════════════════════════════════════════╝"

rm -rf "$D"
