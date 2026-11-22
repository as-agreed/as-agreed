# The plugin's PreToolUse command (hooks.json): start the library's check only where a record may be found, so a
# project with none pays no Python start. It errs toward starting the check; the check itself decides
# (src/as_agreed/locate.py): the project's .as-agreed/binding.json, AS_AGREED_RECORD, or a .as-agreed-record pointer
# in the start folder or a parent. A start folder with a . or .. segment always starts it: the shell follows links
# before .., the check does not, so the two could read different folders.
# Once started, a check that cannot run stops the call (exit 2): Claude Code lets a call through on any other code.
p="${CLAUDE_PROJECT_DIR:-$PWD}"
case "$p" in /*) ;; *) p="$PWD/$p" ;; esac
case "$p/" in */./* | */../*) dotted=1 ;; *) dotted= ;; esac
d="$p"
while [ ! -e "$d/.as-agreed-record" ] && [ ! -L "$d/.as-agreed-record" ] && [ "${d%/*}" != "$d" ]; do
  d="${d%/*}"
done
if [ -n "$dotted" ] || [ -f "$p/.as-agreed/binding.json" ] || [ -n "${AS_AGREED_RECORD:-}" ] ||
   [ -e "$d/.as-agreed-record" ] || [ -L "$d/.as-agreed-record" ]; then
  if ! cd "${CLAUDE_PLUGIN_ROOT}/src" 2>/dev/null; then
    echo "as-agreed could not start the check: the plugin's src folder cannot be reached; the call does not run" >&2
    exit 2
  fi
  CLAUDE_PROJECT_DIR="$p" python3 -m as_agreed hook
  s=$?
  case "$s" in 0 | 2) exit "$s" ;; esac
  echo "as-agreed could not start the check (python3 exit $s); the call does not run" >&2
  exit 2
fi
