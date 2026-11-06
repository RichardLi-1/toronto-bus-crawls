#!/bin/bash
# Runs on every boot of the VM. Installs the collector once, then leaves systemd to keep it alive.
set -eu
A=/opt/ttc
if [ ! -x $A/.venv/bin/python ]; then
  apt-get update -y
  apt-get install -y python3-venv
  mkdir -p $A/collector $A/data/raw
  python3 -m venv $A/.venv
  $A/.venv/bin/pip install -q gtfs-realtime-bindings
fi
curl -sf -H 'Metadata-Flavor: Google' \
  http://metadata.google.internal/computeMetadata/v1/instance/attributes/collect-py -o $A/collector/collect.py
cat > /etc/systemd/system/ttc-collector.service <<UNIT
[Unit]
Description=TTC vehicle position collector
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=$A/.venv/bin/python -I $A/collector/collect.py
WorkingDirectory=$A
Restart=always
RestartSec=15

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable ttc-collector
systemctl restart ttc-collector
