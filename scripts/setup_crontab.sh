#!/bin/sh
# Generic script to translate crons.yaml into a native crontab and start crond.

CONFIG="src/config/crons.yaml"
CRONTAB_FILE="/etc/crontabs/root"

if [ ! -f "$CONFIG" ]; then
    echo "❌ Configuration file not found: $CONFIG" >&2
    exit 1
fi

# Start with an empty crontab
> "$CRONTAB_FILE"

# Extract the first valid timezone safely using grep and head instead of complex yq pipes
CRON_TZ=$(yq e '.crons.*.timezone' "$CONFIG" | grep -v 'null' | grep -v '\-\-\-' | head -n 1)

if [ -n "$CRON_TZ" ]; then
    echo "🌍 Found timezone '$CRON_TZ', will apply to crond environment."
else
    echo "⚠️ No timezone specified in YAML, using system default (UTC)."
    CRON_TZ="UTC"
fi

echo "" >> "$CRONTAB_FILE"

# Loop through all cron jobs safely
for job_key in $(yq e '.crons | keys | .[]' "$CONFIG"); do
    
    ENABLED=$(yq e ".crons.$job_key.enabled" "$CONFIG")
    # Manual fallback for ENABLED
    if [ "$ENABLED" = "null" ]; then ENABLED="true"; fi

    if [ "$ENABLED" = "true" ]; then
        SCHEDULE=$(yq e ".crons.$job_key.schedule" "$CONFIG")
        ENDPOINT=$(yq e ".crons.$job_key.target.endpoint" "$CONFIG")
        METHOD=$(yq e ".crons.$job_key.target.method" "$CONFIG")
        DESCRIPTION=$(yq e ".crons.$job_key.description" "$CONFIG")

        # Manual fallback for METHOD
        if [ "$METHOD" = "null" ]; then METHOD="POST"; fi

        if [ -z "$SCHEDULE" ] || [ "$SCHEDULE" = "null" ] || [ -z "$ENDPOINT" ] || [ "$ENDPOINT" = "null" ]; then
            echo "⚠️ Skipping job '$job_key': missing schedule or endpoint." >&2
            continue
        fi

        # Remove quotes if yq added them
        METHOD=$(echo "$METHOD" | tr -d '"')
        ENDPOINT=$(echo "$ENDPOINT" | tr -d '"')
        SCHEDULE=$(echo "$SCHEDULE" | tr -d '"')

        # Write job entry to crontab
        echo "# $DESCRIPTION" >> "$CRONTAB_FILE"
        echo "$SCHEDULE curl -s -X $METHOD http://lifeos_worker:8080$ENDPOINT -H \"X-Cron-Token:\$SYSTEM_CRON_TOKEN\" > /dev/stdout 2>&1" >> "$CRONTAB_FILE"
        echo "" >> "$CRONTAB_FILE"
        
        echo "✅ Added job: $job_key -> $SCHEDULE ($ENDPOINT)"
    else
        echo "⏭️ Skipping disabled job: $job_key"
    fi
done

# Ensure correct permissions on crontab file
chmod 600 "$CRONTAB_FILE"

echo "--- Generated Crontab ---"
cat "$CRONTAB_FILE"
echo "-------------------------"

echo "🚀 Starting crond with TZ=$CRON_TZ..."
exec env TZ="$CRON_TZ" crond -f -l 2