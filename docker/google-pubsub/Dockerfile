FROM google/cloud-sdk:emulators

CMD ["sh", "-c", \
    "gcloud beta emulators pubsub start --project ${GCP_PROJECT_ID} --data-dir=${PUBSUB_DATA_DIR} --host-port=${PUBSUB_HOST_PORT} & wait"]
