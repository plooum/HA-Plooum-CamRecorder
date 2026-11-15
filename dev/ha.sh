#!/usr/bin/env bash
# Development Home Assistant for Plooum CamRecorder, with a live test camera.
#
#   dev/ha.sh start | stop | status | logs [n] | reset
#
# HA listens on http://127.0.0.1:8130 (HA_PORT), config in dev/config, the integration linked from
# custom_components/. camera.live streams an HLS made live by ffmpeg (served on 8131), camera.still
# has no stream. The Home Assistant venv is HA-Plooum-Cards' (HA_VENV), its token in dev/.ha-token.
set -euo pipefail
DEV="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$DEV")"
CONF="$DEV/config"
VENV="${HA_VENV:-$HOME/git/HA-Plooum-Cards/dev/.venv}"
PORT="${HA_PORT:-8130}"
HLS_PORT="${HLS_PORT:-8131}"
HLS="$DEV/.hls"
LOG="$DEV/ha.log"
export CAMRECORDER_HLS="http://127.0.0.1:$HLS_PORT/live.m3u8" HA_URL="http://127.0.0.1:$PORT"

alive() { [[ -f "$1" ]] && kill -0 "$(cat "$1")" 2>/dev/null; }

seed_http_store() {
  mkdir -p "$CONF/.storage"
  [[ -f "$CONF/.storage/http" ]] && return 0
  cat > "$CONF/.storage/http" <<JSON
{"version": 2, "minor_version": 2, "key": "http", "data": {"stable": {
  "server_host": ["127.0.0.1"], "server_port": $PORT, "cors_allowed_origins": [], "login_attempts_threshold": -1,
  "ip_ban_enabled": true, "ssl_profile": "modern", "use_x_frame_options": true,
  "created_at": "2026-01-01T00:00:00+00:00", "error": null, "error_message": null},
  "pending": null, "yaml_migration_done": true}}
JSON
}

start_hls() {
  mkdir -p "$HLS"
  if ! alive "$HLS/ffmpeg.pid"; then
    rm -f "$HLS"/*.ts "$HLS"/*.m3u8
    nohup ffmpeg -hide_banner -loglevel error -re -f lavfi -i "testsrc2=size=640x360:rate=15" \
      -vf "drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:text='live %{localtime\:%H\\\\\:%M\\\\\:%S}':x=20:y=20:fontsize=32:fontcolor=white:box=1:boxcolor=black" \
      -c:v libx264 -preset veryfast -tune zerolatency -g 30 -pix_fmt yuv420p \
      -f hls -hls_time 2 -hls_list_size 6 -hls_flags delete_segments "$HLS/live.m3u8" \
      >"$HLS/ffmpeg.log" 2>&1 &
    echo $! > "$HLS/ffmpeg.pid"
  fi
  if ! alive "$HLS/http.pid"; then
    nohup python3 -m http.server "$HLS_PORT" --bind 127.0.0.1 --directory "$HLS" >/dev/null 2>&1 &
    echo $! > "$HLS/http.pid"
  fi
}

cmd_start() {
  start_hls
  if alive "$DEV/.ha.pid"; then echo "HA already running — $HA_URL"; return 0; fi
  ln -sfn "$ROOT/custom_components/plooum_camrecorder" "$CONF/custom_components/plooum_camrecorder"
  seed_http_store
  nohup "$VENV/bin/hass" -c "$CONF" --log-file "$LOG" >/dev/null 2>&1 &
  echo $! > "$DEV/.ha.pid"
  for _ in $(seq 120); do curl -sf -o /dev/null "$HA_URL/manifest.json" && break; sleep 1; done
  "$VENV/bin/python" "$DEV/bootstrap.py"
  echo "HA running — $HA_URL"
}

cmd_stop() {
  for f in "$DEV/.ha.pid" "$HLS/ffmpeg.pid" "$HLS/http.pid"; do
    if alive "$f"; then
      pid="$(cat "$f")"; kill "$pid"
      for _ in $(seq 60); do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
    fi
    rm -f "$f"
  done
  echo "stopped"
}

case "${1:-}" in
  start) cmd_start ;;
  stop) cmd_stop ;;
  restart) cmd_stop; cmd_start ;;
  status) alive "$DEV/.ha.pid" && echo "HA running (pid $(cat "$DEV/.ha.pid")) — $HA_URL" || echo "HA stopped" ;;
  logs) tail -n "${2:-50}" "$LOG" ;;
  reset) cmd_stop; git -C "$ROOT" clean -fdX "$CONF" >/dev/null; rm -f "$DEV/.ha-token"; echo "runtime state wiped" ;;
  *) echo "usage: $0 start|stop|restart|status|logs [n]|reset"; exit 2 ;;
esac
