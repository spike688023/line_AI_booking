#!/bin/bash
echo "Running Coffee Shop Agent Tests..."
python -m pytest tests/test_admin.py tests/test_database.py tests/test_agents.py -v
