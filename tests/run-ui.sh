#!/bin/bash
set -Eeuo pipefail

if [[ $# -ne 2 ]]; then
	printf 'Usage: %s APP_IMAGE BROWSER_IMAGE\n' "$0" >&2
	exit 2
fi

app_image=$1
browser_image=$2
app="modembox-ui-app-$$"
browser="modembox-ui-browser-$$"
app_started=false
browser_started=false

log() {
	printf '{"level":"%s","msg":"%s"}\n' "$1" "$2" >&2
}

cleanup() {
	local status=$?
	trap - EXIT
	trap - ERR
	set +e
	if [[ $status -ne 0 ]]; then
		if $browser_started; then docker logs "$browser" >&2; fi
		if $app_started; then docker logs "$app" >&2; fi
	fi
	if $browser_started && ! docker stop "$browser" >/dev/null; then
		log WARN "Could not stop the browser container"
		if [[ $status -eq 0 ]]; then status=1; fi
	fi
	if $app_started && ! docker stop "$app" >/dev/null; then
		log WARN "Could not stop the fixture container"
		if [[ $status -eq 0 ]]; then status=1; fi
	fi
	exit "$status"
}

trap cleanup EXIT
trap 'log ERROR "UI test failed at line $LINENO"' ERR
trap 'exit 130' INT
trap 'exit 143' TERM

docker run --rm -d --init --name "$app" --network none \
	--entrypoint python "$app_image" tests/ui_fixture.py >/dev/null
app_started=true

docker run --rm -d --init --name "$browser" --network "container:$app" \
	--shm-size=256m -e AUTH_TOKEN=EXAMPLE-DO-NOT-USE-browser \
	-e LOG_LEVEL=WARNING "$browser_image" >/dev/null
browser_started=true

docker exec "$app" pytest -q tests/browser_e2e.py
