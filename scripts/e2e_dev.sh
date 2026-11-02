#!/usr/bin/env bash
# Dev smoke test against the running dev stack (port 8100): creates a run from _reqs/tests/*.pdf,
# runs stages 2-4 with BOTH grading prompts on the default Yandex model, prints results and cost.
# Spends real money on the LLM (a few dozen rubles); the run budget cap protects you.
set -euo pipefail
cd "$(dirname "$0")/.."
TMP=$(mktemp -d); J=$TMP/cj; H='X-Requested-With: fedlab'; B=${B:-http://localhost:8100}; M=${MODEL:-yandex-deepseek-v4-flash}
python3 - "$TMP" <<'PY'
import zipfile, glob, os, sys
z = zipfile.ZipFile(sys.argv[1] + "/s.zip", "w")
for i, f in enumerate(sorted(glob.glob("_reqs/tests/*.pdf")), 1):
    z.write(f, f"assign/Команда {i}_{200+i}_assignsubmission_file/{os.path.basename(f)}")
z.close()
PY
curl -s -c $J -o /dev/null "$B/api/auth/dev-login?email=${EMAIL:-vgarshin@yandex.ru}"
pid() { curl -s -b $J $B/api/prompts | python3 -c "import sys,json;print([p['id'] for p in json.load(sys.stdin) if p['name']=='$1'][0])"; }
RID=$(curl -s -b $J -H "$H" -H 'Content-Type: application/json' -d "{\"name\":\"e2e $(date +%H:%M)\",\"config\":{\"grade_variants\":[{\"prompt_id\":$(pid grade_short),\"model_id\":\"$M\"},{\"prompt_id\":$(pid grade_long),\"model_id\":\"$M\"}],\"detect_variants\":[{\"prompt_id\":$(pid ai_detect),\"model_id\":\"$M\"}],\"budget_cap_rub\":300}}" $B/api/runs | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")
curl -s -b $J -H "$H" -F submissions=@$TMP/s.zip $B/api/runs/$RID/upload >/dev/null
until [ "$(curl -s -b $J $B/api/runs/$RID | python3 -c 'import sys,json;print(json.load(sys.stdin)["status"])')" = ready ]; do sleep 2; done
wait_stage() { for _ in $(seq 1 120); do
  n=$(curl -s -b $J $B/api/runs/$RID | python3 -c "import sys,json;s=json.load(sys.stdin)['stages'].get('$1',{});print(s.get('done',0)+s.get('failed',0))"); [ "$n" -ge 5 ] && break; sleep 4; done; }
for st in s2_extract s3_detect s4_grade; do
  curl -s -b $J -H "$H" -H 'Content-Type: application/json' -d '{}' $B/api/runs/$RID/stages/$st/start >/dev/null; wait_stage $st; done
echo "run $RID"; curl -s -b $J $B/api/runs/$RID/teams | python3 -c "
import sys,json
for t in json.load(sys.stdin): print(t['name'][:10].ljust(10), 'AI', t['ai_probability'], '| grade', t['grade_total'], t['stages'].get('s4_grade',{}).get('error') or '')"
curl -s -b $J $B/api/runs/$RID/cost | python3 -c "import sys,json;d=json.load(sys.stdin);print('spent',d['spent_rub'],'RUB; calls',d['calls'],'cache hits',d['cache_hits'])"
