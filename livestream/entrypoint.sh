#!/bin/sh
set -e

CONFIG=/generated/mediamtx.yml

# Start from the base config
cp /mediamtx-base.yml "$CONFIG"

# Append segment/delete overrides from env (apply to pathDefaults)
if [ -n "$RECORD_SEGMENT_DURATION" ]; then
  sed -i "s/recordSegmentDuration: .*/recordSegmentDuration: ${RECORD_SEGMENT_DURATION}/" "$CONFIG"
fi
if [ -n "$RECORD_DELETE_AFTER" ]; then
  sed -i "s/recordDeleteAfter: .*/recordDeleteAfter: ${RECORD_DELETE_AFTER}/" "$CONFIG"
fi

echo "paths:" >> "$CONFIG"

# Iterate over STREAM_1_*, STREAM_2_*, ... until a NAME is missing
i=1
while true; do
  eval "name=\${STREAM_${i}_NAME:-}"
  eval "source_url=\${STREAM_${i}_SOURCE:-}"
  eval "folder=\${STREAM_${i}_FOLDER:-}"

  [ -z "$name" ] && break

  if [ -z "$source_url" ]; then
    echo "WARNING: STREAM_${i}_SOURCE is not set for stream '${name}', skipping." >&2
    i=$((i + 1))
    continue
  fi

  folder="${folder:-$name}"

  echo "  Registering stream ${i}: ${name} <- ${source_url} -> /recordings/${folder}/" >&2

  cat >> "$CONFIG" << EOF

  ${name}:
    source: ${source_url}
    sourceOnDemand: no
    record: yes
    recordPath: /recordings/${folder}/%Y-%m-%d_%H-%M-%S-%f
EOF

  i=$((i + 1))
done

if [ "$i" -eq 1 ]; then
  echo "ERROR: No streams defined. Set STREAM_1_NAME, STREAM_1_SOURCE, and STREAM_1_FOLDER in your .env file." >&2
  exit 1
fi

echo "Config written to $CONFIG with $((i - 1)) stream(s)." >&2
