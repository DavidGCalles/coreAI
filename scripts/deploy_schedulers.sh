#!/bin/bash
# Despliegue minimalista de Cloud Scheduler para LifeOS

CONFIG="src/config/crons.yaml"

# 1. Validaciones previas
if [ ! -f "$CONFIG" ]; then
    echo "❌ No se encuentra el archivo: $CONFIG"
    exit 1
fi

if [ -z "$WORKER_URL" ] || [ -z "$SYSTEM_CRON_TOKEN" ] || [ -z "$GCP_SERVICE_ACCOUNT_EMAIL" ] || [ -z "$GCP_LOCATION" ]; then
    echo "❌ Faltan variables de entorno."
    echo "Asegúrate de definir: WORKER_URL, SYSTEM_CRON_TOKEN, GCP_SERVICE_ACCOUNT_EMAIL, GCP_LOCATION (ej. europe-southwest1)"
    exit 1
fi

echo "🚀 Iniciando despliegue en Google Cloud Scheduler (Región: $GCP_LOCATION)..."

# 2. Iterar sobre el YAML usando yq
for job_key in $(yq e '.crons | keys | .[]' "$CONFIG"); do
    
    ENABLED=$(yq e ".crons.$job_key.enabled" "$CONFIG")
    if [ "$ENABLED" != "true" ] && [ "$ENABLED" != "null" ]; then
        echo "⏭️  Omitiendo $job_key (Deshabilitado)"
        continue
    fi

    SCHEDULE=$(yq e ".crons.$job_key.schedule" "$CONFIG")
    ENDPOINT=$(yq e ".crons.$job_key.target.endpoint" "$CONFIG")
    METHOD=$(yq e ".crons.$job_key.target.method" "$CONFIG" | tr -d '"')
    TZ_VAL=$(yq e ".crons.$job_key.timezone" "$CONFIG" | tr -d '"')
    DESC=$(yq e ".crons.$job_key.description" "$CONFIG" | tr -d '"')

    # Fallbacks de configuración
    [ "$METHOD" = "null" ] && METHOD="POST"
    [ "$TZ_VAL" = "null" ] && TZ_VAL="UTC"
    [ "$DESC" = "null" ] && DESC="LifeOS Worker Task"

    JOB_NAME="lifeos-$job_key"
    FULL_URL="${WORKER_URL}${ENDPOINT}"

    echo "⚙️  Configurando: $JOB_NAME -> $SCHEDULE ($FULL_URL)"

    # 3. La magia de gcloud: Intentar actualizar, si falla, crear.
    # Usamos oidc-service-account-email para que GCP inyecte el token OIDC automáticamente y pase la seguridad de Cloud Run.
    
    gcloud scheduler jobs update http "$JOB_NAME" \
        --location="$GCP_LOCATION" \
        --schedule="$SCHEDULE" \
        --time-zone="$TZ_VAL" \
        --uri="$FULL_URL" \
        --http-method="$METHOD" \
        --headers="X-Cron-Token=$SYSTEM_CRON_TOKEN" \
        --oidc-service-account-email="$GCP_SERVICE_ACCOUNT_EMAIL" \
        --description="$DESC" \
        --quiet 2>/dev/null

    if [ $? -eq 0 ]; then
        echo "✅ Actualizado: $JOB_NAME"
    else
        # Si falló la actualización (probablemente porque no existe), lo creamos
        gcloud scheduler jobs create http "$JOB_NAME" \
            --location="$GCP_LOCATION" \
            --schedule="$SCHEDULE" \
            --time-zone="$TZ_VAL" \
            --uri="$FULL_URL" \
            --http-method="$METHOD" \
            --headers="X-Cron-Token=$SYSTEM_CRON_TOKEN" \
            --oidc-service-account-email="$GCP_SERVICE_ACCOUNT_EMAIL" \
            --description="$DESC" \
            --quiet
        
        if [ $? -eq 0 ]; then
            echo "✅ Creado: $JOB_NAME"
        else
            echo "❌ Error al configurar: $JOB_NAME"
        fi
    fi
done

echo "🎉 Despliegue finalizado."