#!/bin/bash
# Pull temporalio/ui:2.31 through explicit mirrors until one serves an intact layer.
for m in hub.rat.dev docker.1panel.live docker.hlmirror.com docker.unsee.tech docker.chenby.cn docker.imgdb.de; do
  echo "== trying $m"
  if timeout 180 docker pull "$m/temporalio/ui:2.31"; then
    docker tag "$m/temporalio/ui:2.31" temporalio/ui:2.31 && echo "TAGGED_OK from $m"
    exit 0
  fi
done
echo "ALL_MIRRORS_FAILED"
exit 1
