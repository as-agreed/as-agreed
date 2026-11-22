import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { Band } from './types'

// The As-Agreed band, above the prompt: what the plugin's hook wrote for this task in
// .as-agreed/runs/<session>/band.json (src/as_agreed/band.py, from the task's ledger.jsonl). Nothing here counts:
// it reads that file, draws its labels, and its "Board" button opens board.html from the same folder.
// Needs Claude Code 2.1.290 or later, in the terminal or the desktop app; elsewhere the hook's own line still shows.
const band = atom({ plugin: 'as-agreed', key: 'band' } as const, null)

// The folder band.py's run_folder() names for a session.
export const runFolder = (root: string, session: string) =>
  `${root}/.as-agreed/runs/${session.replace(/[^A-Za-z0-9_-]/g, '_') || 'session'}`

const folder = async ($: EngineInterface) => runFolder(await $.session.root(), await $.session.id())

// A task with no checked step, or a file that cannot be read, shows no band.
async function refresh($: EngineInterface) {
  const text = await $.fs.read(`${await folder($)}/band.json`).catch(() => '')
  let next: Band | null = null
  try {
    const parsed = text === '' ? null : JSON.parse(text)
    next = parsed !== null && Array.isArray(parsed.labels) ? (parsed as Band) : null
  } catch {
    next = null
  }
  await update($, band, () => next)
}

// The page is a local file that loads nothing; the system's own opener shows it.
async function openBoard($: EngineInterface) {
  const file = `${await folder($)}/board.html`
  const windows = (await $.env.get('OS')) === 'Windows_NT'
  const mac = !windows && (await $.process.run(['uname'])).stdout.trim() === 'Darwin'
  await $.process.run(windows ? ['cmd', '/c', 'start', '', file] : [mac ? 'open' : 'xdg-open', file])
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const started = await next(e)
    await refresh($).catch(() => undefined)
    return started
  }).catch(($, e, next) => next(e))

  // /clear, /resume and /branch go on under another session id: another task, another band.
  on('command.run', { command: ['clear', 'resume', 'branch'] }, async ($, e, next) => {
    const result = await next(e)
    await refresh($).catch(() => undefined)
    return result
  }).catch(($, e, next) => next(e))

  // The hook checks the call beneath this one (PreToolUse) and writes band.json: read it once the call is done.
  on('tool.call', async ($, e, next) => {
    const ran = await next(e)
    await refresh($).catch(() => undefined)
    return ran
  }).catch(($, e, next) => next(e))

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const b = await read($, band)
    if (e.props.hasSurvey || b === null) return next(e)
    // Other mods share this slot: theirs is drawn below ours.
    const below = await next(e).catch(() => undefined)

    const { Box, Text, Button } = $.ui.resolve(e)
    const width = e.surface === 'terminal' ? e.props.bodyColumns : undefined
    const [kept, held, unchecked, through] = b.labels
    const board = () => openBoard($).catch(() => $.ui.toast('as-agreed: could not open the Board page'))

    const mine = (
      <Box flexDirection="column" width={width}>
        <Box flexDirection="row" alignItems="center" gap={2}>
          <Text bold color={b.terms > 0 ? 'success' : undefined}>
            {kept}
          </Text>
          {held !== undefined && (
            <Text bold={b.held > 0} color={b.held > 0 ? 'warning' : undefined} dimColor={b.held === 0}>
              {held}
            </Text>
          )}
          {unchecked !== undefined && (
            <Text bold={b.unchecked > 0} dimColor={b.unchecked === 0}>
              {unchecked}
            </Text>
          )}
          {through !== undefined && (
            <Text bold color="error">
              {through}
            </Text>
          )}
          <Box flexGrow={1} />
          <Button key="board" plain label="Board" onPress={board} />
        </Box>
        {b.why !== '' && (
          <Text dimColor wrap="truncate-end">
            {b.why}
          </Text>
        )}
      </Box>
    )
    return below ? (
      <Box flexDirection="column">
        {mine}
        {below}
      </Box>
    ) : (
      mine
    )
  }).catch(($, e, next) => next(e))
}
