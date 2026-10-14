#!/bin/bash
cd "$(dirname "$0")"
PYTHONPATH=src exec venv/bin/python -m agent.main config.toml
