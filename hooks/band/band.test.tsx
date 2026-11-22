import type { On } from 'claude-code'
import { expect, test } from 'claude-code/testing'

// The states src/as_agreed/band.py writes for tests/fixtures/band/<case>.ledger.jsonl; tests/test_band.py checks
// that these three lines stay what Python writes.
const KEPT = {"labels": ["Kept as agreed 2 of 2", "held 0", "unchecked 0"], "terms": 2, "kept": 2, "held": 0, "changed_not_held": 0, "unchecked": 0, "why": ""}
const HELD = {"labels": ["Kept as agreed 1 of 2", "held 1", "unchecked 0"], "terms": 2, "kept": 1, "held": 1, "changed_not_held": 0, "unchecked": 0, "why": "pay limit.amount WIDENED: 19800 where at most 18400 USD was agreed"}
const UNCHECKED = {"labels": ["Kept as agreed 1 of 2", "held 0", "unchecked 1"], "terms": 2, "kept": 1, "held": 0, "changed_not_held": 0, "unchecked": 1, "why": "1 term waits for your read"}

const ROOT = '/project'
const SESSION = 'fixture-held'
const FOLDER = `${ROOT}/.as-agreed/runs/${SESSION}`

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

// Nothing sits beneath the plugin: these hooks stand for the engine and the project's files.
function engine(on: On, files: Record<string, string>, ran: string[][] = []) {
  on('session.root', () => ({ value: ROOT }))
  on('session.id', () => ({ value: SESSION }))
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('fs.read', (_, e) => {
    const text = files[e.path]
    return text !== undefined ? { value: text } : { deny: 'no such file' }
  })
  on('env.get', () => ({ value: undefined }))
  on('process.run', (_, e) => {
    ran.push([...e.argv])
    const stdout = e.argv[0] === 'uname' ? 'Darwin\n' : ''
    return { value: { exitCode: 0, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })
  return ran
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

test('Board opens the page beside band.json', async ($, on) => {
  const ran = engine(on, { [`${FOLDER}/band.json`]: JSON.stringify(HELD) })
  await $.session.start({ cwd: ROOT, surface: 'terminal', isInteractive: true })

  const ui = await $.ui.mount({ ...BAND, surface: 'terminal' })
  await ui.press({ key: 'board' })

  expect(ran[ran.length - 1]).toEqual(['open', `${FOLDER}/board.html`])
  await ui.unmount()
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
