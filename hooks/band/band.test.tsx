import type { On } from 'claude-code'
import { expect, test } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'

// The states src/as_agreed/band.py writes for tests/fixtures/band/<case>.ledger.jsonl; tests/test_band.py checks
// that these three lines stay what Python writes.
const KEPT = {"labels": ["Kept as agreed 2 of 2", "held 0", "unchecked 0"], "terms": 2, "kept": 2, "held": 0, "changed_not_held": 0, "unchecked": 0, "why": ""}
const HELD = {"labels": ["Kept as agreed 1 of 2", "held 1", "unchecked 0"], "terms": 2, "kept": 1, "held": 1, "changed_not_held": 0, "unchecked": 0, "why": "pay limit.amount WIDENED: 19800 where at most 18400 USD was agreed"}
const UNCHECKED = {"labels": ["Kept as agreed 1 of 2", "held 0", "unchecked 1"], "terms": 2, "kept": 1, "held": 0, "changed_not_held": 0, "unchecked": 1, "why": "1 term waits for your read"}

const ROOT = '/project'
const SESSION = 'fixture-held'
const PLUGIN = '/plugins/as-agreed'
const PAGE = '/tmp/as-agreed-board-1.html'
const runOf = (root: string) => `${root}/.as-agreed/runs/${SESSION}`
const FOLDER = runOf(ROOT)

const BAND = {
  plugin: 'as-agreed',
  component: 'AbovePrompt',
  props: {
    hasSurvey: false,
    isCollapsed: false,
    isWorking: false,
    maxRows: 20,
    bodyColumns: 120,
    scroll: { offset: 0, bodyRows: 20 },
    view: {},
  },
} as const

type Ran = { argv: string[]; cwd?: string }
type Setup = { root?: string; os?: string; page?: string; built?: number }

// Nothing sits beneath the plugin: these hooks stand for the engine, the project's files and the library's command.
function engine(on: On, files: Record<string, string>, setup: Setup = {}) {
  const ran: Ran[] = []
  const toasts: string[] = []
  on('session.root', () => ({ value: setup.root ?? ROOT }))
  on('session.id', () => ({ value: SESSION }))
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('fs.read', (_, e) => {
    const text = files[e.path]
    return text !== undefined ? { value: text } : { deny: 'no such file' }
  })
  on('env.get', (_, e) => ({ value: e.name === 'OS' ? setup.os : undefined }))
  on('process.hookEnv', () => ({ value: { CLAUDE_PLUGIN_ROOT: PLUGIN } }))
  on('ui.toast', (_, e) => {
    toasts.push(e.text)
    return { value: undefined }
  })
  on('process.run', (_, e) => {
    ran.push({ argv: [...e.argv], cwd: e.init?.cwd })
    const built = e.argv[0] === 'python3'
    const exitCode = built ? setup.built ?? 0 : 0
    const stdout = e.argv[0] === 'uname' ? 'Darwin\n' : built && exitCode === 0 ? JSON.stringify({ ...HELD, board: setup.page ?? PAGE }) : ''
    return { value: { exitCode, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })
  return { ran, toasts }
}

async function pressBoard($: Engine, root = ROOT) {
  await $.session.start({ cwd: root, surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ ...BAND, surface: 'terminal' })
  await ui.press({ key: 'board' })
  await ui.unmount()
}

for (const [name, state] of [['kept', KEPT], ['held', HELD], ['unchecked', UNCHECKED]] as const) {
  test(`the ${name} task shows the three labels of the mock`, async ($, on) => {
    engine(on, { [`${FOLDER}/band.json`]: JSON.stringify(state) })
    await $.session.start({ cwd: ROOT, surface: 'terminal', isInteractive: true })

    for (const surface of ['terminal', 'desktop'] as const) {
      const ui = await $.ui.mount({ ...BAND, surface })
      for (const label of state.labels) {
        expect((await ui.find({ type: 'Text', text: label }))?.text).toBe(label)
      }
      if (state.why !== '') expect((await ui.find({ type: 'Text', text: state.why }))?.text).toBe(state.why)
      expect(await ui.find({ type: 'Button', key: 'board' })).toBeDefined()
      await ui.unmount()
    }
  })
}

test('Board has the library build the page at the click, and opens that page', async ($, on) => {
  const { ran } = engine(on, { [`${FOLDER}/band.json`]: JSON.stringify(HELD) })
  await pressBoard($)

  expect(ran[0]).toEqual({
    argv: ['python3', '-m', 'as_agreed', 'band', `${FOLDER}/ledger.jsonl`, '--board-temp'],
    cwd: `${PLUGIN}/src`,
  })
  expect(ran[ran.length - 1]?.argv).toEqual(['open', PAGE])
  // the run folder's board.html, which the agent can rewrite, is never opened
  expect(ran.some(r => r.argv.includes(`${FOLDER}/board.html`))).toBe(false)
})

test('a page that could not be built opens nothing', async ($, on) => {
  const { ran, toasts } = engine(on, { [`${FOLDER}/band.json`]: JSON.stringify(HELD) }, { built: 2 })
  await pressBoard($)

  expect(ran.map(r => r.argv[0])).toEqual(['python3'])
  expect(toasts).toEqual(['as-agreed: could not open the Board page'])
})

test('on Windows, Board opens the page with explorer, its slashes turned, never through cmd', async ($, on) => {
  const page = 'C:/Users/u/AppData/Local/Temp/as-agreed-board-1.html'
  const { ran } = engine(on, { [`${FOLDER}/band.json`]: JSON.stringify(HELD) }, { os: 'Windows_NT', page })
  await pressBoard($)

  expect(ran[ran.length - 1]?.argv).toEqual(['explorer.exe', 'C:\\Users\\u\\AppData\\Local\\Temp\\as-agreed-board-1.html'])
  expect(ran.some(r => r.argv[0] === 'cmd')).toBe(false)
})

test('on Windows, a project root with & runs no cmd', async ($, on) => {
  // the engine reads a path as this machine's; what matters is the `&`, which only the library's argv carries
  const root = '/work/x&calc&'
  const { ran } = engine(on, { [`${runOf(root)}/band.json`]: JSON.stringify(HELD) }, { root, os: 'Windows_NT' })
  await pressBoard($, root)

  expect(ran[0]?.argv[4]).toBe(`${runOf(root)}/ledger.jsonl`) // an argument of the library's command, no shell reads it
  expect(ran.some(r => r.argv[0] === 'cmd')).toBe(false)
  expect(ran[ran.length - 1]?.argv[0]).toBe('explorer.exe')
})

test('on Windows, a page path that cmd would read opens nothing and shows the path', async ($, on) => {
  const page = 'C:/Users/a&b/AppData/Local/Temp/as-agreed-board-1.html'
  const { ran, toasts } = engine(on, { [`${FOLDER}/band.json`]: JSON.stringify(HELD) }, { os: 'Windows_NT', page })
  await pressBoard($)

  expect(ran.map(r => r.argv[0])).toEqual(['python3'])
  expect(toasts).toEqual([`as-agreed: open the Board page yourself: ${page}`])
})

test('a task with no checked step shows no band', async ($, on) => {
  engine(on, {})
  await $.session.start({ cwd: ROOT, surface: 'terminal', isInteractive: true })

  const ui = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await ui.find({ type: 'Button', key: 'board' })).toBeUndefined()
  await ui.unmount()
})

test('a file that is not the band shows no band', async ($, on) => {
  engine(on, { [`${FOLDER}/band.json`]: '{"labels": ' })
  await $.session.start({ cwd: ROOT, surface: 'terminal', isInteractive: true })

  const ui = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await ui.find({ type: 'Button', key: 'board' })).toBeUndefined()
  await ui.unmount()
})
