#!/usr/bin/env bash
# MongoDB Primary에 mongosh 접속 헬퍼
docker compose exec mongo-primary mongosh \
    -u admin -p "${MONGO_ROOT_PASSWORD:-agentoe_dev_pass}" \
    --authenticationDatabase admin \
    agentoe "$@"
