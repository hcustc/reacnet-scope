#!/usr/bin/env bash

set -Eeuo pipefail

RS_PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
RS_USERNAME="$(id -un)"
RS_USER_HOME="${HOME:-/home/${RS_USERNAME}}"
RS_HOST="${REACNET_SCOPE_HOST:-127.0.0.1}"
RS_PORT="${REACNET_SCOPE_PORT:-8060}"
RS_DATASET_PATH="${REACNET_SCOPE_STARTUP_DATASET:-/media/${RS_USERNAME}/T3000/rng_data_2500K}"
RS_REQUIRED_ROOTS="${RS_USER_HOME}:/media/${RS_USERNAME}:/data:/mnt"
RS_EXTRA_ROOTS="${REACNET_SCOPE_EXTRA_ROOTS:-}"
RS_CLI="${RS_PROJECT_DIR}/.venv/bin/reacnet-scope"

export REACNET_SCOPE_COMPACT_NAV="${REACNET_SCOPE_COMPACT_NAV:-1}"

if [[ -n "${RS_EXTRA_ROOTS}" ]]; then
    export REACNET_SCOPE_ALLOWED_ROOTS="${RS_REQUIRED_ROOTS}:${RS_EXTRA_ROOTS}"
else
    export REACNET_SCOPE_ALLOWED_ROOTS="${RS_REQUIRED_ROOTS}"
fi

cd -- "${RS_PROJECT_DIR}"

if [[ ! -x "${RS_CLI}" ]]; then
    if ! command -v uv >/dev/null 2>&1; then
        echo "错误：未找到 .venv/bin/reacnet-scope，也未安装 uv。" >&2
        echo "请先安装 uv，然后重新运行本脚本。" >&2
        exit 1
    fi
    echo "首次运行：正在根据 uv.lock 创建项目环境……"
    uv sync --locked
    if [[ ! -x "${RS_CLI}" ]]; then
        echo "错误：依赖安装完成后仍未找到 ${RS_CLI}。" >&2
        exit 1
    fi
fi

echo "ReacNet Scope 启动配置"
echo "  项目目录：${RS_PROJECT_DIR}"
echo "  访问地址：http://${RS_HOST}:${RS_PORT}"
echo "  紧凑导航开关：${REACNET_SCOPE_COMPACT_NAV}"
echo "  数据目录：${RS_DATASET_PATH}"
echo "  允许根目录：${REACNET_SCOPE_ALLOWED_ROOTS}"

if [[ -d "${RS_DATASET_PATH}" && -r "${RS_DATASET_PATH}" && -x "${RS_DATASET_PATH}" ]]; then
    echo "  数据状态：目录已挂载且可访问"
else
    echo "  数据状态：当前未发现或无法访问；软件仍会启动，挂载磁盘后可直接刷新选择器。" >&2
fi

if [[ "${1:-}" == "--check" ]]; then
    echo "自检完成，未启动服务器。"
    exit 0
fi

if [[ $# -gt 0 ]]; then
    echo "用法：${0##*/} [--check]" >&2
    exit 2
fi

echo "按 Ctrl+C 可停止服务。"
exec "${RS_CLI}" serve --host "${RS_HOST}" --port "${RS_PORT}"
