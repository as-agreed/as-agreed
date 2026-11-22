# The plugin's PreToolUse command (hooks.json): start the library's check only where a record may be found, so a
# project with none pays no Python start. It errs toward starting the check; the check itself decides
# (src/as_agreed/locate.py): the project's .as-agreed/binding.json, AS_AGREED_RECORD, or a .as-agreed-record pointer
# in the start folder or a parent.
p="${CLAUDE_PROJECT_DIR:-$PWD}"
case "$p" in /*) ;; *) p="$PWD/$p" ;; esac
d="$p"
while [ ! -e "$d/.as-agreed-record" ] && [ ! -L "$d/.as-agreed-record" ] && [ "${d%/*}" != "$d" ]; do
  d="${d%/*}"
done
if [ -f "$p/.as-agreed/binding.json" ] || [ -n "${AS_AGREED_RECORD:-}" ] ||
   [ -e "$d/.as-agreed-record" ] || [ -L "$d/.as-agreed-record" ]; then
  cd "${CLAUDE_PLUGIN_ROOT}/src" && CLAUDE_PROJECT_DIR="$p" exec python3 -m as_agreed hook
fi
