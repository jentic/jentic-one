#!/bin/bash
# scripts/start-stack.sh — Automated script to spin up the Jentic Mini + Barrikade Docker stack.

# ANSI Colors
GREEN="\033[92m"
CYAN="\033[96m"
BOLD="\033[1m"
RESET="\033[0m"

echo -e "${BOLD}${CYAN}=== Spinning Up Jentic Mini & Barrikade Core Stack ===${RESET}\n"

# Start the Docker Compose stack using the Barrikade overlay
docker compose -f compose.yml -f compose.barrikade.yml up -d

echo -e "\n${GREEN}[OK] Jentic Mini & Barrikade Core started successfully!${RESET}"
echo -e "  - Jentic Mini Gateway: ${BOLD}http://localhost:8900${RESET}"
echo -e "  - Barrikade Core API:  ${BOLD}http://localhost:8000${RESET}"

echo -e "\nTo verify prompt injection and response scanning, run:"
echo -e "  ${BOLD}python3 scripts/test_real_barrikade.py${RESET}\n"
