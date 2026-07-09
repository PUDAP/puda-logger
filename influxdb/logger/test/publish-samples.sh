#!/bin/sh
# Publish sample PUDA messages for Telegraf integration testing.

NATS_URL="${NATS_URL:-nats://localhost:4222}"

publish() {
  subject="$1"
  payload="$2"
  nats pub "$subject" "$payload" --server "$NATS_URL"
}

publish 'puda.first.cmd.immediate' '{"timestamp":"2026-07-07T09:41:42.035851888Z","subject":"puda.first.cmd.immediate","machine_id":"first","category":"cmd","topic":"immediate","data":{"header":{"version":"1.0","message_type":"command","user_id":"4ad51e38-52ea-456d-9e56-535bf5f08c49","username":"zhao","machine_id":"first","run_id":"55b0d6a3-0fd6-4aaf-a001-584f2ed5bf9b","timestamp":"2026-07-07T09:41:42Z"},"command":{"name":"start","params":{},"step_number":0,"version":"1.0","machine_id":"first"}}}'

publish 'puda.first.cmd.response.immediate' '{"timestamp":"2026-07-07T09:41:42.076049529Z","subject":"puda.first.cmd.response.immediate","machine_id":"first","category":"cmd","topic":"response.immediate","data":{"header":{"version":"1.0","message_type":"response","user_id":"4ad51e38-52ea-456d-9e56-535bf5f08c49","username":"zhao","machine_id":"first","run_id":"55b0d6a3-0fd6-4aaf-a001-584f2ed5bf9b","timestamp":"2026-07-07T09:41:42Z"},"command":{"name":"start","params":{},"kwargs":{},"step_number":0,"version":"1.0","machine_id":"first"},"response":{"status":"success","completed_at":"2026-07-07T09:41:42Z","code":null,"message":null,"data":null}}}'

publish 'puda.first.cmd.queue' '{"timestamp":"2026-07-07T09:41:42.089682553Z","subject":"puda.first.cmd.queue","machine_id":"first","category":"cmd","topic":"queue","data":{"header":{"version":"1.0","message_type":"command","user_id":"4ad51e38-52ea-456d-9e56-535bf5f08c49","username":"zhao","machine_id":"first","run_id":"55b0d6a3-0fd6-4aaf-a001-584f2ed5bf9b","timestamp":"2026-07-07T09:41:42Z"},"command":{"name":"home","params":{},"step_number":1,"version":"1.0","machine_id":"first"}}}'

publish 'puda.first.cmd.response.queue' '{"timestamp":"2026-07-07T09:41:53.492590981Z","subject":"puda.first.cmd.response.queue","machine_id":"first","category":"cmd","topic":"response.queue","data":{"header":{"version":"1.0","message_type":"response","user_id":"4ad51e38-52ea-456d-9e56-535bf5f08c49","username":"zhao","machine_id":"first","run_id":"55b0d6a3-0fd6-4aaf-a001-584f2ed5bf9b","timestamp":"2026-07-07T09:41:53Z"},"command":{"name":"home","params":{},"kwargs":{},"step_number":1,"version":"1.0","machine_id":"first"},"response":{"status":"success","completed_at":"2026-07-07T09:41:53Z","code":null,"message":null,"data":null}}}'

publish 'puda.first.tlm.health' '{"timestamp":"2026-07-07T09:50:11.80501189Z","subject":"puda.first.tlm.health","machine_id":"first","category":"tlm","topic":"health","data":{"timestamp":"2026-07-07T09:50:11Z","cpu":6.0,"mem":23.9,"temp":69.0}}'

echo "Published 5 sample messages to $NATS_URL"
