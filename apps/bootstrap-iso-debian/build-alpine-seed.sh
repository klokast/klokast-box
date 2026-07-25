#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  build-alpine-seed.sh \
    [--release 3.23.3] \
    [--arch x86_64] \
    [--iso-url URL | --iso-file /path/to/alpine-standard.iso] \
    [--output-dir /path/to/output]

Build a versioned Alpine diskless seed bundle for Ansible playbook 12.

The output tarball contains:
  - boot/vmlinuz-lts
  - boot/initramfs-lts
  - boot/modloop-lts
  - apks/<arch>/

This is the payload needed by Alpine to boot.
EOF
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    printf 'Missing required command: %s\n' "$1" >&2
    exit 1
  }
}

release='3.23.3'
arch='x86_64'
iso_url=''
iso_file=''
output_dir=''

while [ "$#" -gt 0 ]; do
  case "$1" in
    --release)
      release="${2:?missing value for --release}"
      shift 2
      ;;
    --arch)
      arch="${2:?missing value for --arch}"
      shift 2
      ;;
    --iso-url)
      iso_url="${2:?missing value for --iso-url}"
      shift 2
      ;;
    --iso-file)
      iso_file="${2:?missing value for --iso-file}"
      shift 2
      ;;
    --output-dir)
      output_dir="${2:?missing value for --output-dir}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown argument: %s\n' "$1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [ -n "$iso_url" ] && [ -n "$iso_file" ]; then
  printf 'Use either --iso-url or --iso-file, not both\n' >&2
  exit 1
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
output_dir="${output_dir:-${script_dir}/../artifacts/alpine-seed}"
mkdir -p "$output_dir"

if [ -z "$iso_url" ] && [ -z "$iso_file" ]; then
  minor="${release%.*}"
  iso_url="http://mirrors.hust.edu.cn/alpine/v${minor}/releases/${arch}/alpine-standard-${release}-${arch}.iso"
fi

require_cmd curl
require_cmd tar
require_cmd sha256sum
require_cmd sha512sum
require_cmd xorriso

work_dir="$(mktemp -d "${TMPDIR:-/tmp}/alpine-seed.XXXXXX")"
trap 'rm -rf "$work_dir"' EXIT

if [ -n "$iso_file" ]; then
  [ -r "$iso_file" ] || {
    printf 'ISO file is not readable: %s\n' "$iso_file" >&2
    exit 1
  }
  iso_path="$iso_file"
else
  iso_path="${work_dir}/alpine-standard-${release}-${arch}.iso"
  curl -fL "$iso_url" -o "$iso_path"
fi

extract_dir="${work_dir}/seed"
mkdir -p "${extract_dir}/boot" "${extract_dir}/apks/${arch}"

xorriso -osirrox on -indev "$iso_path" -extract /boot/vmlinuz-lts "${extract_dir}/boot/vmlinuz-lts" >/dev/null 2>&1
xorriso -osirrox on -indev "$iso_path" -extract /boot/initramfs-lts "${extract_dir}/boot/initramfs-lts" >/dev/null 2>&1
xorriso -osirrox on -indev "$iso_path" -extract /boot/modloop-lts "${extract_dir}/boot/modloop-lts" >/dev/null 2>&1
xorriso -osirrox on -indev "$iso_path" -extract "/apks/${arch}" "${extract_dir}/apks/${arch}" >/dev/null 2>&1

for path in \
  "${extract_dir}/boot/vmlinuz-lts" \
  "${extract_dir}/boot/initramfs-lts" \
  "${extract_dir}/boot/modloop-lts" \
  "${extract_dir}/apks/${arch}/APKINDEX.tar.gz"
do
  [ -e "$path" ] || {
    printf 'Missing expected extracted path: %s\n' "$path" >&2
    exit 1
  }
done

tarball="${output_dir}/alpine-standard-${release}-${arch}.tar.gz"
tar -C "${extract_dir}" -czf "$tarball" .
(
  cd "$output_dir"
  sha256sum "alpine-standard-${release}-${arch}.tar.gz" >"alpine-standard-${release}-${arch}.tar.gz.sha256"
  sha512sum "alpine-standard-${release}-${arch}.tar.gz" >"alpine-standard-${release}-${arch}.tar.gz.sha512"
)

printf 'Built %s\n' "$tarball"
