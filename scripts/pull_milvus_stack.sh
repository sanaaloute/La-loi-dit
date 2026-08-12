#!/bin/bash
# Retry-pull the Milvus stack images until they succeed (flaky network).
for img in quay.io/coreos/etcd:v3.5.14 minio/minio:RELEASE.2024-05-28T17-19-04Z milvusdb/milvus:v2.6.0; do
  for i in $(seq 1 60); do
    if docker pull "$img"; then
      echo "PULLED $img"
      break
    fi
    echo "retry $i for $img"
    sleep 10
  done
done
echo ALL_DONE
