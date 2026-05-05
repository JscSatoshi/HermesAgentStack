#!/bin/sh
set -eu

HERMES_HOME="${HERMES_HOME:-/opt/data}"
INSTALL_DIR="/opt/hermes"
BOOTSTRAP_DIR="/bootstrap"

if [ "$(id -u)" = "0" ]; then
    if [ -n "${HERMES_GID:-}" ] && [ "${HERMES_GID}" != "$(id -g hermes)" ]; then
        groupmod -o -g "${HERMES_GID}" hermes 2>/dev/null || true
    fi

    if [ -n "${HERMES_UID:-}" ] && [ "${HERMES_UID}" != "$(id -u hermes)" ]; then
        usermod -o -u "${HERMES_UID}" hermes 2>/dev/null || true
    fi

    mkdir -p "${HERMES_HOME}" "${HERMES_HOME}/home"
    chown hermes:hermes "${HERMES_HOME}" "${HERMES_HOME}/home" 2>/dev/null || true

    if [ -f "${BOOTSTRAP_DIR}/config.yaml" ]; then
        cp "${BOOTSTRAP_DIR}/config.yaml" "${HERMES_HOME}/config.yaml"
        chown hermes:hermes "${HERMES_HOME}/config.yaml" 2>/dev/null || true
        chmod 640 "${HERMES_HOME}/config.yaml" 2>/dev/null || true
    fi

    if [ -f "${BOOTSTRAP_DIR}/SOUL.md" ]; then
        cp "${BOOTSTRAP_DIR}/SOUL.md" "${HERMES_HOME}/SOUL.md"
        chown hermes:hermes "${HERMES_HOME}/SOUL.md" 2>/dev/null || true
        chmod 644 "${HERMES_HOME}/SOUL.md" 2>/dev/null || true
    fi

    exec gosu hermes "$0" "$@"
fi

mkdir -p \
    "${HERMES_HOME}/cron" \
    "${HERMES_HOME}/sessions" \
    "${HERMES_HOME}/logs" \
    "${HERMES_HOME}/hooks" \
    "${HERMES_HOME}/memories" \
    "${HERMES_HOME}/skills" \
    "${HERMES_HOME}/skins" \
    "${HERMES_HOME}/plans" \
    "${HERMES_HOME}/workspace" \
    "${HERMES_HOME}/home"

[ -f "${HERMES_HOME}/.env" ] || : > "${HERMES_HOME}/.env"

export HOME="${HERMES_HOME}/home"
export PATH="${INSTALL_DIR}/.venv/bin:${PATH}"

if [ "$#" -gt 0 ] && command -v "$1" >/dev/null 2>&1; then
    exec /usr/bin/tini -g -- "$@"
fi

exec /usr/bin/tini -g -- hermes "$@"
