#!/bin/bash

export http_proxy=http://127.0.0.1:13416
export https_proxy=http://127.0.0.1:13416
export ALL_PROXY=socks5://127.0.0.1:19426

cd "$(dirname "$0")"

echo "Starting Polymarket Scalper with proxy..."
echo "Proxy: http://127.0.0.1:13416"

mkdir -p data logs

python3 src/main.py
