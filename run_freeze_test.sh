#!/bin/bash
# Orchestriert die Freeze-Teststufen: startet, wartet, killt, klassifiziert.
cd "D:/AntiGravitySoftware/GitWorkspace/FritzBoxMonitor" || exit 1
PYW=".venv/Scripts/pythonw.exe"
LOG="test_freeze.log"

run_stage() {
  local stage=$1
  echo "==============================================="
  echo "STUFE $stage: Start $(date +%H:%M:%S)"
  : > "$LOG"
  "$PYW" test_freeze.py --stage "$stage" &
  local pid=$!
  local done=0
  for i in $(seq 1 24); do   # bis 48s
    sleep 2
    if grep -q "TEST DONE" "$LOG" 2>/dev/null; then done=1; break; fi
    if ! kill -0 $pid 2>/dev/null; then break; fi
  done
  if [ $done -eq 1 ]; then
    echo "ERGEBNIS STUFE $stage: OK (Test beendet sich selbst)"
  else
    echo "ERGEBNIS STUFE $stage: EINGEFROREN (kein TEST DONE nach 48s)"
  fi
  echo "--- Log der Stufe $stage:"
  cat "$LOG"
  # Prozessbaum killen (trampoline + echter python + webview2)
  local winpid
  winpid=$(cat /proc/$pid/winpid 2>/dev/null || echo "$pid")
  taskkill //PID "$winpid" //F //T >/dev/null 2>&1
  sleep 2
}

for s in "$@"; do run_stage "$s"; done
echo "==============================================="
echo "Alle Stufen abgeschlossen: $(date +%H:%M:%S)"
