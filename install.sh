#!/bin/bash
set -euo pipefail
# modembox-managed-command

readonly marker='# Managed by the ModemBox installer'
readonly command_marker='# modembox-managed-command'
readonly installer_url=https://raw.githubusercontent.com/psyb0t/docker-modembox/main/install.sh
readonly system_command=/usr/local/bin/modembox
temporary=''

log() {
	printf '[%s] %s\n' "$1" "$2" >&2
}

fail() {
	log ERROR "$1"
	exit 1
}

trap 'log ERROR "Installer failed at line $LINENO"' ERR
trap 'if [[ -n "$temporary" && -f "$temporary" ]]; then rm -- "$temporary"; fi' EXIT

usage() {
	printf '%s\n' 'Usage: bash install.sh [--user|--system] [--version latest|local|vX.Y.Z]' \
		'       bash install.sh [--user|--system] --uninstall [--purge-data]'
}

command_usage() {
	printf '%s\n' 'Usage: modembox start|stop|status|logs' \
		'       modembox upgrade [--version latest|local|vX.Y.Z]' \
		'       modembox uninstall [--purge-data]'
}

write_compose() {
	local project="$1" tag="$2" group node gid
	local -a groups=()
	local -A seen=()
	for node in /dev/ttyUSB*; do
		[[ -c "$node" ]] || continue
		gid="$(stat -c %g -- "$node")"
		if [[ ! -v "seen[$gid]" ]]; then
			seen[$gid]=1
			groups+=("$gid")
		fi
	done
	if ((${#groups[@]} == 0)) && group="$(getent group dialout)"; then
		IFS=: read -r _ _ gid _ <<<"$group"
		[[ "$gid" =~ ^[0-9]+$ ]] || fail 'Invalid dialout group ID.'
		groups+=("$gid")
	fi

	cat <<EOF
$marker
name: $project
services:
  modembox:
    image: psyb0t/modembox:$tag
    user: "$runtime_user"
    environment:
      MODEMBOX_TOKEN: "\${MODEMBOX_TOKEN:-}"
      MODEMBOX_DISABLE_AUTH: "\${MODEMBOX_DISABLE_AUTH:-false}"
    init: true
    restart: unless-stopped
    ports:
      - "127.0.0.1:8000:8000"
    read_only: true
    tmpfs:
      - /tmp:rw,noexec,nosuid,size=64m
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    pids_limit: 256
    deploy:
      resources:
        limits:
          memory: 512M
          cpus: "1.0"
          pids: 256
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "5"
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).close()"]
      interval: 10s
      timeout: 3s
      retries: 5
      start_period: 30s
    device_cgroup_rules:
      - "c 188:* rw"
    volumes:
      - type: bind
        source: /dev
        target: /dev
        read_only: true
      - type: bind
        source: ./data
        target: /data
        bind:
          create_host_path: false
EOF
	if [[ "$tag" == local ]]; then
		printf '    pull_policy: never\n'
	fi
	if ((${#groups[@]} > 0)); then
		printf '    group_add:\n'
		printf '      - "%s"\n' "${groups[@]}"
	fi

	cat <<'EOF'
  tailscale:
    image: tailscale/tailscale:v1.102.5@sha256:c507f3a2a6ab1cabd8d809b98edeb41edbd5c3fb6ad9632ffd098b4c7d0b4065
    profiles: [tailscale]
    network_mode: service:modembox
    depends_on:
      modembox:
        condition: service_healthy
        restart: true
    init: true
    restart: unless-stopped
    read_only: true
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    tmpfs:
      - /tmp:rw,noexec,nosuid,size=16m
      - /var/run:rw,noexec,nosuid,size=8m
    mem_limit: 256m
    cpus: 0.5
    pids_limit: 128
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "5"
    environment:
      TS_AUTHKEY: "${MODEMBOX_TAILSCALE_AUTHKEY:-}"
      TS_HOSTNAME: "${MODEMBOX_TAILSCALE_HOSTNAME:-modembox}"
      TS_AUTH_ONCE: "true"
      TS_USERSPACE: "true"
      TS_EXTRA_ARGS: --accept-dns=false
      TS_STATE_DIR: /var/lib/tailscale
      TS_SERVE_CONFIG: /config/serve.json
      TS_ENABLE_HEALTH_CHECK: "true"
      TS_LOCAL_ADDR_PORT: 127.0.0.1:9002
    healthcheck:
      test: ["CMD", "wget", "-q", "-O", "/dev/null", "http://127.0.0.1:9002/healthz"]
      interval: 10s
      timeout: 3s
      retries: 5
      start_period: 30s
    volumes:
      - tailscale-state:/var/lib/tailscale
      - type: bind
        source: ./tailscale
        target: /config
        read_only: true
        bind:
          create_host_path: false
volumes:
  tailscale-state:
EOF
}

write_tailscale_config() {
	install -d -m 0755 -- "$tailscale_dir"
	temporary="$(mktemp "$tailscale_dir/.serve.XXXXXX")"
	cat >"$temporary" <<'EOF'
{
  "TCP": {
    "443": { "HTTPS": true }
  },
  "Web": {
    "${TS_CERT_DOMAIN}:443": {
      "Handlers": {
        "/": { "Proxy": "http://127.0.0.1:8000" }
      }
    }
  }
}
EOF
	chmod 0644 -- "$temporary"
	mv -T -- "$temporary" "$tailscale_dir/serve.json"
}

set_paths() {
	local mode="$1" digest first_line
	if [[ "$mode" == system ]]; then
		((EUID == 0)) || fail 'Run system install and management commands with sudo.'
		config=/etc/modembox
		command_file="$system_command"
		runtime_user=10001:10001
	else
		((EUID != 0)) || fail 'Run a user install as your normal Linux user, or select --system.'
		config="${XDG_CONFIG_HOME:-$HOME/.config}/modembox"
		command_file="$HOME/.local/bin/modembox"
		runtime_user="$EUID:$(id -g)"
	fi
	[[ "$config" == /* ]] || fail 'XDG_CONFIG_HOME must be an absolute path.'
	compose_file="$config/compose.yaml"
	env_file="$config/.env"
	data_dir="$config/data"
	tailscale_dir="$config/tailscale"
	[[ ! -L "$config" && ! -L "$compose_file" && ! -L "$env_file" && ! -L "$data_dir" && ! -L "$command_file" ]] ||
		fail 'Refusing to use a symlink for a ModemBox installation path.'
	[[ ! -L "$tailscale_dir" ]] || fail 'Refusing to use a symlink for the Tailscale configuration.'
	if [[ -e "$command_file" ]]; then
		if [[ ! -f "$command_file" ]] || ! grep -Fxq "$command_marker" "$command_file"; then
			fail 'The existing modembox command was not installed by ModemBox.'
		fi
	fi

	current_tag=latest
	if [[ -e "$compose_file" ]]; then
		[[ -f "$compose_file" ]] || fail 'The Compose path must be a regular file.'
		if ! read -r first_line <"$compose_file" || [[ "$first_line" != "$marker" ]]; then
			fail 'The existing Compose file was not generated by the ModemBox installer.'
		fi
		current_tag="$(sed -n 's/^    image: psyb0t\/modembox://p' "$compose_file")"
	fi
	digest="$(realpath -m -- "$config" | sha256sum)"
	project="modembox-${digest:0:12}"
}

validate_tag() {
	local tag="$1"
	[[ "$tag" == latest || "$tag" == local || "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9.-]+)?$ ]] ||
		fail 'Use latest, local or a version tag such as v1.2.3.'
}

compose() {
	local base="$1" dotenv=/dev/null
	shift
	local -a options=(--project-name "$project" --project-directory "$config" -f "$base")
	if [[ -f "$env_file" ]]; then dotenv="$env_file"; fi
	if [[ -f "$config/compose.override.yaml" ]]; then
		options+=(-f "$config/compose.override.yaml")
	fi
	docker compose "${options[@]}" --env-file "$dotenv" "$@"
}

save_compose() {
	local tag="$1"
	temporary="$(mktemp "$config/.compose.XXXXXX")"
	write_compose "$project" "$tag" >"$temporary"
	compose "$temporary" --profile tailscale config --quiet
	mv -T -- "$temporary" "$compose_file"
}

configure() {
	local tag="$1" token
	umask 077
	install -d -m 0700 -- "$config"
	install -d -m 0700 -- "$data_dir"
	if ((EUID == 0)); then
		chown "$runtime_user" -- "$data_dir"
	fi
	if [[ ! -e "$env_file" ]]; then
		token="$(openssl rand -hex 32)"
		(
			set -o noclobber
			printf 'MODEMBOX_TOKEN=%s\n' "$token" >"$env_file"
		)
	fi
	[[ -f "$env_file" ]] || fail 'The token file must be a regular file.'
	chmod 0600 -- "$env_file"
	write_tailscale_config
	save_compose "$tag"
}

uninstall_stack() {
	local purge="$1" file state_volume found_volume
	if [[ -f "$compose_file" ]]; then
		compose "$compose_file" --profile tailscale down
		rm -- "$compose_file"
	fi
	if [[ -f "$command_file" ]]; then rm -- "$command_file"; fi
	if [[ -f "$tailscale_dir/serve.json" && ! -L "$tailscale_dir/serve.json" ]]; then
		rm -- "$tailscale_dir/serve.json"
	fi
	if [[ "$purge" == true ]]; then
		state_volume="${project}_tailscale-state"
		found_volume="$(docker volume ls --filter "name=^${state_volume}$" --format '{{.Name}}')"
		if [[ "$found_volume" == "$state_volume" ]]; then
			docker volume rm "$state_volume"
		fi
		for file in "$env_file" "$data_dir"/modembox.sqlite3 "$data_dir"/modembox.sqlite3-wal \
			"$data_dir"/modembox.sqlite3-shm "$data_dir"/pin.key "$data_dir"/modembox.log{,.1,.2,.3}; do
			if [[ -f "$file" && ! -L "$file" ]]; then rm -- "$file"; fi
		done
		log INFO 'Removed the ModemBox token, data files, and Tailscale state.'
	else
		log INFO 'Kept the token, data directory, and Tailscale state for reinstall.'
	fi
}

start_stack() {
	local enabled existing
	enabled="$(compose "$compose_file" config --environment | sed -n 's/^MODEMBOX_TAILSCALE_ENABLED=//p')" ||
		fail 'Could not read the Compose environment.'
	case "$enabled" in
	true)
		compose "$compose_file" --profile tailscale up -d "$@" modembox tailscale
		;;
	false | '')
		existing="$(compose "$compose_file" --profile tailscale ps -aq tailscale)"
		if [[ -n "$existing" ]]; then
			compose "$compose_file" --profile tailscale rm -s -f tailscale
		fi
		compose "$compose_file" up -d "$@" modembox
		;;
	*) fail 'MODEMBOX_TAILSCALE_ENABLED must be true or false.' ;;
	esac
}

upgrade_stack() {
	local tag="$1"
	validate_tag "$tag"
	if [[ "$tag" == local ]]; then
		docker image inspect psyb0t/modembox:local >/dev/null ||
			fail 'Build psyb0t/modembox:local before upgrading to it.'
	else
		docker pull "psyb0t/modembox:$tag"
	fi
	if [[ "$tag" != "$current_tag" ]]; then save_compose "$tag"; fi
	start_stack --pull missing
}

manage() {
	local action="${1:-}" mode=user tag purge=false
	case "$action" in
	'' | -h | --help)
		command_usage
		return
		;;
	start | stop | status) [[ $# == 1 ]] || fail 'This command takes no arguments.' ;;
	logs) ;;
	upgrade) [[ $# == 1 || ($# == 3 && "$2" == --version && -n "$3") ]] || fail 'Use upgrade [--version TAG].' ;;
	uninstall)
		[[ $# == 1 || ($# == 2 && "$2" == --purge-data) ]] || fail 'Use uninstall [--purge-data].'
		if [[ $# == 2 ]]; then purge=true; fi
		;;
	*)
		command_usage >&2
		exit 2
		;;
	esac
	if [[ "$(realpath -- "${BASH_SOURCE[0]}")" == "$system_command" ]]; then mode=system; fi
	set_paths "$mode"
	[[ -f "$compose_file" ]] || fail 'ModemBox is missing its Compose file. Rerun the installer.'
	case "$action" in
	start) start_stack ;;
	stop) compose "$compose_file" --profile tailscale stop ;;
	status) compose "$compose_file" --profile tailscale ps -a ;;
	logs)
		shift
		compose "$compose_file" --profile tailscale logs "$@"
		;;
	upgrade)
		tag="${3:-$current_tag}"
		upgrade_stack "$tag"
		;;
	uninstall) uninstall_stack "$purge" ;;
	esac
}

install_command() {
	local mode=user tag='' uninstall=false purge=false
	while (($# > 0)); do
		case "$1" in
		-h | --help)
			usage
			return
			;;
		--user | --system)
			mode="${1#--}"
			shift
			;;
		--version)
			[[ $# -ge 2 && -n "$2" ]] || fail 'Pass one image tag with --version.'
			tag="$2"
			shift 2
			;;
		--uninstall)
			uninstall=true
			shift
			;;
		--purge-data)
			purge=true
			shift
			;;
		*)
			usage >&2
			exit 2
			;;
		esac
	done
	if $purge && ! $uninstall; then fail '--purge-data requires --uninstall.'; fi
	if $uninstall && [[ -n "$tag" ]]; then fail '--uninstall cannot be combined with --version.'; fi
	set_paths "$mode"
	docker compose version >/dev/null || fail 'Docker Compose is required.'
	if $uninstall; then
		uninstall_stack "$purge"
		return
	fi
	tag="${tag:-$current_tag}"
	validate_tag "$tag"
	configure "$tag"
	local bin_directory source="${BASH_SOURCE[0]:-}"
	bin_directory="$(dirname -- "$command_file")"
	if [[ ! -d "$bin_directory" ]]; then install -d -m 0755 -- "$bin_directory"; fi
	temporary="$(mktemp "$bin_directory/.modembox.XXXXXX")"
	if [[ -n "$source" && -f "$source" ]]; then
		install -m 0755 -T -- "$source" "$temporary"
	else
		curl -fsSL --proto '=https' --output "$temporary" "$installer_url" ||
			fail 'Could not download the ModemBox command.'
		if ! grep -Fxq "$command_marker" "$temporary" || ! bash -n "$temporary"; then
			fail 'The downloaded ModemBox command is invalid.'
		fi
		chmod 0755 -- "$temporary"
	fi
	mv -T -- "$temporary" "$command_file"
	if [[ "$mode" == system ]]; then
		printf 'Start ModemBox with: sudo modembox start\n'
	else
		printf 'Start ModemBox with: %q start\n' "$command_file"
	fi
}

if [[ "${0##*/}" == modembox ]]; then
	manage "$@"
else
	install_command "$@"
fi
