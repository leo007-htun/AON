#!/bin/bash
cd "$(dirname "$0")"
PYTHONPATH=src exec venv/bin/python -m aon.main config.toml
