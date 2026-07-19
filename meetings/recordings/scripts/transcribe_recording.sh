#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Transcribe an OBS meeting recording using the wolf virtualenv.

Usage:
  scripts/transcribe_recording.sh [AUDIO_FILE]

Fixed behavior:
  - Reads OBS files from recordings/ when AUDIO_FILE is omitted.
  - If multiple files exist, asks which one to transcribe.
  - Downmixes all audio and transcribes once with the large-v3 model, Japanese.
  - Writes only *.transcript.md.

Examples:
  scripts/transcribe_recording.sh
  scripts/transcribe_recording.sh recordings/meeting.mp4
EOF
}

SELF_PATH="${BASH_SOURCE[0]}"
if [[ "$SELF_PATH" != /* ]]; then
  SELF_PATH="$PWD/$SELF_PATH"
fi

if [[ -z "${TRANSCRIBE_RECORDING_IN_WOLF:-}" ]]; then
  exec bash -ic 'cd "$1" || exit; shift; workon wolf || exit; TRANSCRIBE_RECORDING_IN_WOLF=1 exec "$@"' \
    bash "$PWD" "$SELF_PATH" "$@"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

AUDIO_FILE=""
RECORDINGS_DIR="$ROOT_DIR/recordings"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h)
      usage
      exit 0
      ;;
    -*)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
    *)
      if [[ -n "$AUDIO_FILE" ]]; then
        echo "Only one AUDIO_FILE can be provided." >&2
        exit 2
      fi
      AUDIO_FILE="$1"
      shift
      ;;
  esac
done

choose_recording() {
  local dir="$1"
  local files=()
  local file choice

  if [[ ! -d "$dir" ]]; then
    echo "Recordings directory not found: $dir" >&2
    exit 1
  fi

  while IFS= read -r file; do
    files+=("$file")
  done < <(
    find "$dir" -maxdepth 2 -type f \
      \( -iname '*.m4a' -o -iname '*.mp3' -o -iname '*.wav' -o -iname '*.flac' -o -iname '*.aac' -o -iname '*.mp4' -o -iname '*.mkv' -o -iname '*.mov' -o -iname '*.webm' -o -iname '*.m4v' \) \
      -print | sort -r
  )

  if [[ "${#files[@]}" -eq 0 ]]; then
    echo "No audio files found in: $dir" >&2
    exit 1
  fi

  if [[ "${#files[@]}" -eq 1 ]]; then
    printf '%s\n' "${files[0]}"
    return 0
  fi

  echo "Multiple recordings found. Choose one to transcribe:" >&2
  local i
  for i in "${!files[@]}"; do
    printf '  %2d) %s\n' "$((i + 1))" "${files[$i]#$ROOT_DIR/}" >&2
  done
  echo >&2

  while true; do
    read -r -p "Enter number: " choice
    if [[ "$choice" =~ ^[0-9]+$ ]] &&
      (( choice >= 1 && choice <= ${#files[@]} )); then
      printf '%s\n' "${files[$((choice - 1))]}"
      return 0
    fi
    echo "Please enter a number from 1 to ${#files[@]}." >&2
  done
}

if [[ -z "$AUDIO_FILE" ]]; then
  AUDIO_FILE="$(choose_recording "$RECORDINGS_DIR")"
fi

if [[ ! -f "$AUDIO_FILE" ]]; then
  echo "Audio file not found: $AUDIO_FILE" >&2
  exit 1
fi

echo "Transcribing with wolf environment..."
echo "  Audio:    ${AUDIO_FILE#$ROOT_DIR/}"
echo "  Output:   ${AUDIO_FILE%.*}.transcript.md"
echo

python "$ROOT_DIR/scripts/transcribe_audio.py" "$AUDIO_FILE"
