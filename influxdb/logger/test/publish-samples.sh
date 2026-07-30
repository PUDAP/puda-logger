#!/bin/sh
# Publish sample PUDA messages for Telegraf integration testing.

set -eu
NATS_URL="${NATS_URL:-nats://localhost:4222}"
case "${NATS_URL#*://}" in
  *@*) echo "NATS URL userinfo is not supported by sample publisher" >&2; exit 1 ;;
esac

publish() {
  subject="$1"
  payload="$2"
  nats pub "$subject" "$payload" --server "$NATS_URL"
}

publish puda.first.cmd.immediate '{"header":{"version":"1.0","message_type":"command","user_id":"4ad51e38-52ea-456d-9e56-535bf5f08c49","username":"zhao","machine_id":"first","run_id":"55b0d6a3-0fd6-4aaf-a001-584f2ed5bf9b","timestamp":"2026-07-07T09:41:42Z"},"command":{"name":"start","params":{},"step_number":0,"version":"1.0","machine_id":"first"}}'

publish puda.first.cmd.response.immediate '{"header":{"version":"1.0","message_type":"response","user_id":"4ad51e38-52ea-456d-9e56-535bf5f08c49","username":"zhao","machine_id":"first","run_id":"55b0d6a3-0fd6-4aaf-a001-584f2ed5bf9b","timestamp":"2026-07-07T09:41:42Z"},"command":{"name":"start","params":{},"kwargs":{},"step_number":0,"version":"1.0","machine_id":"first"},"response":{"status":"success","completed_at":"2026-07-07T09:41:42Z","code":null,"message":null,"data":null}}'

publish puda.first.cmd.queue '{"header":{"version":"1.0","message_type":"command","user_id":"4ad51e38-52ea-456d-9e56-535bf5f08c49","username":"zhao","machine_id":"first","run_id":"55b0d6a3-0fd6-4aaf-a001-584f2ed5bf9b","timestamp":"2026-07-07T09:41:42Z"},"command":{"name":"home","params":{},"step_number":1,"version":"1.0","machine_id":"first"}}'

publish puda.first.cmd.response.queue '{"header":{"version":"1.0","message_type":"response","user_id":"4ad51e38-52ea-456d-9e56-535bf5f08c49","username":"zhao","machine_id":"first","run_id":"55b0d6a3-0fd6-4aaf-a001-584f2ed5bf9b","timestamp":"2026-07-07T09:41:53Z"},"command":{"name":"home","params":{},"kwargs":{},"step_number":1,"version":"1.0","machine_id":"first"},"response":{"status":"success","completed_at":"2026-07-07T09:41:53Z","code":null,"message":null,"data":null}}'

publish puda.first.tlm.health '{"timestamp":"2026-07-07T09:50:11Z","opcua_connected":true,"stale":false,"configured_points":498,"resolved_points":498,"cached_points":498,"sqlite_queue_depth":0,"sqlite_dropped_items":0,"sqlite_writer_ok":true,"sqlite_writer_error_count":0,"sqlite_writer_last_error_type":null,"outbox_pending":0,"outbox_published":42,"outbox_failed_pending":0,"reconnect_count":0,"publisher_error_count":0,"publisher_last_error":null}'

publish puda.first.tlm.ald.point '{"schema":"puda.ald.telemetry.v1","event_id":"11111111-1111-4111-8111-111111111111","event_time":"2026-07-07T09:51:00.100000000Z","machine_id":"first","point_key":"tube_1.cycle_count","node_id":"ns=4;s=Objects/ALD/Tube1/CycleCount","scope":"tube","tube":"tube_1","category":"counter","value_type":"int","value_int":42,"quality":"Good","unit":"count","source_timestamp":"2026-07-07T09:51:00.080000000Z","server_timestamp":"2026-07-07T09:51:00.090000000Z","received_at":"2026-07-07T09:51:00.100000000Z","catalog_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}'

publish puda.first.tlm.ald.point '{"schema":"puda.ald.telemetry.v1","event_id":"22222222-2222-4222-8222-222222222222","event_time":"2026-07-07T09:51:01.100000000Z","machine_id":"first","point_key":"tube_1.temperature","node_id":"ns=4;s=Objects/ALD/Tube1/Temperature","scope":"tube","tube":"tube_1","category":"temperature","value_type":"float","value_float":182.75,"quality":"Good","unit":"degC","source_timestamp":"2026-07-07T09:51:01.080000000Z","server_timestamp":"2026-07-07T09:51:01.090000000Z","received_at":"2026-07-07T09:51:01.100000000Z","catalog_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}'

publish puda.first.tlm.ald.point '{"schema":"puda.ald.telemetry.v1","event_id":"33333333-3333-4333-8333-333333333333","event_time":"2026-07-07T09:51:02.100000000Z","machine_id":"first","point_key":"tube_1.pump_running","node_id":"ns=4;s=Objects/ALD/Tube1/PumpRunning","scope":"tube","tube":"tube_1","category":"state","value_type":"bool","value_bool":true,"quality":"Good","unit":"state","source_timestamp":"2026-07-07T09:51:02.080000000Z","server_timestamp":"2026-07-07T09:51:02.090000000Z","received_at":"2026-07-07T09:51:02.100000000Z","catalog_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}'

publish puda.first.tlm.ald.point '{"schema":"puda.ald.telemetry.v1","event_id":"44444444-4444-4444-8444-444444444444","event_time":"2026-07-07T09:51:03.100000000Z","machine_id":"first","point_key":"tube_1.recipe_state","node_id":"ns=4;s=Objects/ALD/Tube1/RecipeState","scope":"tube","tube":"tube_1","category":"state","value_type":"string","value_text":"deposition","quality":"Good","unit":"state","source_timestamp":"2026-07-07T09:51:03.080000000Z","server_timestamp":"2026-07-07T09:51:03.090000000Z","received_at":"2026-07-07T09:51:03.100000000Z","catalog_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}'

publish puda.first.tlm.ald.alarm '{"schema":"puda.ald.telemetry.v1","event_id":"55555555-5555-4555-8555-555555555555","event_time":"2026-07-07T09:52:00.100000000Z","machine_id":"first","point_key":"tube_1.temperature_high","node_id":"ns=4;s=Objects/ALD/Tube1/TemperatureHigh","scope":"tube","tube":"tube_1","category":"temperature","value_type":"bool","value_bool":true,"quality":"Good","unit":"state","source_timestamp":"2026-07-07T09:52:00.080000000Z","server_timestamp":"2026-07-07T09:52:00.090000000Z","received_at":"2026-07-07T09:52:00.100000000Z","catalog_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","active":true,"severity":"critical","description":"Tube 1 temperature is above the safety threshold"}'

publish puda.first.tlm.ald.alarm '{"schema":"puda.ald.telemetry.v1","event_id":"66666666-6666-4666-8666-666666666666","event_time":"2026-07-07T09:53:00.100000000Z","machine_id":"first","point_key":"tube_1.temperature_high","node_id":"ns=4;s=Objects/ALD/Tube1/TemperatureHigh","scope":"tube","tube":"tube_1","category":"temperature","value_type":"bool","value_bool":false,"quality":"Good","unit":"state","source_timestamp":"2026-07-07T09:53:00.080000000Z","server_timestamp":"2026-07-07T09:53:00.090000000Z","received_at":"2026-07-07T09:53:00.100000000Z","catalog_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","active":false,"severity":"critical","description":"Tube 1 temperature returned below the safety threshold"}'

echo "Published 11 sample messages"
