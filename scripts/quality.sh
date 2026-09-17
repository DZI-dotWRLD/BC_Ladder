#!/usr/bin/env sh
set -eu

skip_full_tests=false
skip_audit=false
for argument in "$@"; do
    case "$argument" in
        --skip-full-tests) skip_full_tests=true ;;
        --skip-audit) skip_audit=true ;;
        *) echo "Unknown option: $argument" >&2; exit 2 ;;
    esac
done

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
python="$repo_root/venv/bin/python"
pip_audit="$repo_root/venv/bin/pip-audit"

if [ ! -x "$python" ]; then
    echo "Missing local virtual environment. Create it with: python3.14 -m venv venv" >&2
    exit 1
fi

cd "$repo_root"
"$python" manage.py check
"$python" manage.py makemigrations --check --dry-run
"$python" -m ruff format --check .
"$python" -m ruff check .
git diff --check

if [ "$skip_full_tests" = false ]; then
    "$python" manage.py test --noinput
fi

if [ "$skip_audit" = false ]; then
    if [ -x "$pip_audit" ]; then
        "$pip_audit" -r requirements.txt
    else
        "$python" -m pip_audit -r requirements.txt
    fi
fi
