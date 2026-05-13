#!/bin/bash

cd "$(dirname "$0")"

echo "Starting Polymarket Sports Scalper..."

mkdir -p data logs

python3 src/main.py
