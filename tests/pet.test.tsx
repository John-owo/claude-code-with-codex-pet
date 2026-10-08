import { expect, mock, test } from 'claude-code/testing'

const strip = (frames: number) => ({
  frames: Array.from({ length: frames }, (_, i) => `data:image/webp;base64,AAA${i}`),
  durations: Array.from({ length: frames }, () => 100),
  w: 96,
  h: 104,
})

const BAKED = {
  ok: true,
  pet: { id: 'guga', displayName: '咕嘎', followsCodex: true },
  available: [
    { id: 'guga', displayName: '咕嘎' },
    { id: 'monthly-salary-cat', displayName: 'Monthly salary cat' },
  ],
  states: Object.fromEntries(
    ['idle', 'waving', 'jumping', 'failed', 'waiting', 'running', 'review'].map(s => [s, strip(6)]),
  ),
}

// The band's props as the engine passes them; the pet only reads bodyColumns.
const BAND = {
  component: 'AbovePrompt',
  props: { bodyColumns: 80, hasSurvey: false, isWorking: false, maxRows: 8 } as never,
} as const

test('the Codex pet shows above the prompt and follows the turn', async ($, on) => {
  const clock = mock.clock(on)
  mock.store(on, { hidden: false }) // as after /pet show
  mock.env(on, { USERPROFILE: 'C:/Users/test' })
  const written = new Map<string, string>()
  // The engine hands paths over in the platform's own spelling.
  const sharedState = () =>
    JSON.parse([...written].find(([path]) => /[\\/]\.codex-pet[\\/]sessions[\\/]cc-[^\\/]+\.json$/.test(path))?.[1] ?? '{}')
  on('fs.write', ($, e) => {
    written.set(e.path, e.text)
    return { value: undefined }
  })
  const argvs: string[][] = []
  on('process.run', ($, e) => {
    argvs.push([...e.argv])
    return {
      value: {
        exitCode: 0,
        stdout: JSON.stringify(BAKED),
        stderr: '',
        isStdoutTruncated: false,
        isStderrTruncated: false,
      },
    }
  })

  on('command.register', () => ({ value: {} as never }))
  on('session.start', ($, e) => ({ cwd: e.cwd }))
  on('session.id', () => ({ value: 'sess-1' }))
  on('command.run', () => ({ text: 'engine' }))
  on('turn.start', ($, e) => ({ turnId: e.turnId }))
  on('turn.complete', ($, e) => ({ text: e.answer }))

  await $.session.start({ cwd: '/tmp', surface: 'desktop', isInteractive: true })
  expect(argvs[0]).toContain('--pet')
  expect(argvs[0]![argvs[0]!.length - 1]).toBe('auto')
  // The desktop pet is started detached through Start-Process, so the call returns at once.
  expect(
    argvs.some(a => a[0] === 'powershell' && /Start-Process 'pyw'.*pet_overlay\.py/.test(a.join(' '))),
  ).toBe(true)
  expect(sharedState()).toMatchObject({ agent: 'cc', status: 'idle', mood: 'waving', petId: 'guga' })

  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ plugin: 'codex-pet', surface, ...BAND })
    expect(await ui.find({ type: 'Text', text: /咕嘎 · 嗨/ })).toBeDefined()
    if (surface === 'desktop') expect(await ui.find({ type: 'Svg' })).toBeDefined()
    await ui.unmount()
  }

  await clock.advance(3000)
  const ui = await $.ui.mount({ plugin: 'codex-pet', surface: 'desktop', ...BAND })
  expect(await ui.find({ type: 'Text', text: /待命中/ })).toBeDefined()
  expect(sharedState().mood).toBe('idle')

  // A turn: running while it works, ready with what it said once it answers.
  const turn = { text: '幫我整理資料夾', turnId: 't1' } as never
  await $.turn.start(turn)
  expect(sharedState()).toMatchObject({ status: 'running', firstPrompt: '幫我整理資料夾' })
  await $.turn.complete({ reason: 'answer', answer: '**整理好了**，共 3 個資料夾', durationMs: 10, turnId: 't1' } as never)
  expect(sharedState()).toMatchObject({ status: 'ready', action: '整理好了，共 3 個資料夾' })

  // The mod's own timer steps the frames: the drawn image changes over time.
  const before = JSON.stringify(await ui.find({ type: 'Svg' }))
  await clock.advance(300)
  expect(JSON.stringify(await ui.find({ type: 'Svg' }))).not.toBe(before)

  // /pet overlay off leaves the desktop pet its close request in the shared folder.
  const off = await $.command.run({ command: 'pet', args: 'overlay off', origin: { kind: 'composer' } } as never)
  expect(JSON.stringify(off)).toContain('關閉')
  expect([...written.keys()].some(path => /[\\/]\.codex-pet[\\/]takeover$/.test(path))).toBe(true)

  // /pet-off and /pet-on are standalone commands doing the same thing.
  written.clear()
  const offCmd = await $.command.run({ command: 'pet-off', args: '', origin: { kind: 'composer' } } as never)
  expect(JSON.stringify(offCmd)).toContain('關閉')
  expect([...written.keys()].some(path => /[\\/]\.codex-pet[\\/]takeover$/.test(path))).toBe(true)
  const onCmd = await $.command.run({ command: 'pet-on', args: '', origin: { kind: 'composer' } } as never)
  expect(JSON.stringify(onCmd)).toContain('開啟')

  // Picked from the desktop app's list they arrive as the commands/*.md names.
  written.clear()
  const listedOff = await $.command.run({ command: 'codex-pet:pet-off', args: '', origin: { kind: 'composer' } } as never)
  expect(JSON.stringify(listedOff)).toContain('關閉')
  expect([...written.keys()].some(path => /[\\/]\.codex-pet[\\/]takeover$/.test(path))).toBe(true)
  const listedOn = await $.command.run({ command: 'codex-pet:pet-on', args: '', origin: { kind: 'composer' } } as never)
  expect(JSON.stringify(listedOn)).toContain('開啟')

  const listed = await $.command.run({ command: 'pet', args: 'list', origin: { kind: 'composer' } } as never)
  expect(JSON.stringify(listed)).toContain('monthly-salary-cat')

  const hidden = await $.command.run({ command: 'pet', args: 'hide', origin: { kind: 'composer' } } as never)
  expect(JSON.stringify(hidden)).toContain('收起來')
  await ui.unmount()
})

test('/pet-size and /pet-mode leave the desktop pet a request it applies and remembers', async ($, on) => {
  mock.clock(on)
  mock.store(on)
  mock.env(on, { USERPROFILE: 'C:/Users/test' })
  // A small file system in memory; the engine hands paths over in the platform's own spelling.
  const files = new Map<string, string>()
  const key = (path: string) => path.replace(/\\/g, '/').replace(/^.*\/\.codex-pet\//, '')
  on('fs.write', ($, e) => {
    files.set(key(e.path), e.text)
    return { value: undefined }
  })
  on('fs.read', ($, e, next) => (files.has(key(e.path)) ? { value: files.get(key(e.path))! } : next(e)))
  on('process.run', () => ({
    value: { exitCode: 0, stdout: JSON.stringify(BAKED), stderr: '', isStdoutTruncated: false, isStderrTruncated: false },
  }))
  on('command.register', () => ({ value: {} as never }))
  on('session.start', ($, e) => ({ cwd: e.cwd }))
  on('session.id', () => ({ value: 'sess-3' }))
  on('command.run', () => ({ text: 'engine' }))
  const run = async (command: string, args = '') =>
    JSON.stringify(await $.command.run({ command, args, origin: { kind: 'composer' } } as never))
  const request = () => JSON.parse(files.get('request.json') ?? '{}')

  await $.session.start({ cwd: '/tmp', surface: 'desktop', isInteractive: true })

  // With no argument they say what the pet has now: here prefs from before sizes were percentages.
  files.set('overlay.json', JSON.stringify({ size: '大', anchor: [100, 100] }))
  expect(await run('pet-size')).toContain('129%')
  expect(await run('pet-mode')).toContain('懸停')

  expect(await run('pet-size', '150')).toContain('150%')
  expect(request()).toEqual({ size: 150 })
  // A second setting before the pet took the first keeps both.
  expect(await run('codex-pet:pet-mode', '物理')).toContain('物理')
  expect(request()).toEqual({ size: 150, mode: 'physics' })
  expect(await run('pet-size')).toContain('150%')
  expect(await run('pet-mode', '切換')).toContain('懸停')
  expect(request().mode).toBe('hover')

  // The pet took the request and saved it in its prefs.
  files.delete('request.json')
  files.set('overlay.json', JSON.stringify({ size: 87, mode: 'physics' }))
  expect(await run('pet', 'size')).toContain('87%')
  expect(await run('pet', 'mode')).toContain('物理')

  // Sizes as words, through /pet too; nonsense and out-of-range sizes change nothing.
  expect(await run('pet', 'size 小')).toContain('75%')
  expect(await run('codex-pet:pet-size', 'large')).toContain('130%')
  expect(await run('pet-size', '200%')).toContain('200%')
  expect(request()).toEqual({ size: 200 })
  for (const bad of ['1000', '10', 'huge']) {
    expect(await run('pet-size', bad)).toContain('不是可以用的大小')
  }
  expect(await run('pet-mode', 'sideways')).toContain('沒有「sideways」')
  expect(request()).toEqual({ size: 200 })
})

test('with the desktop pet on, the band above the prompt stays off', async ($, on) => {
  mock.clock(on)
  mock.store(on)
  mock.env(on, { USERPROFILE: 'C:/Users/test' })
  on('fs.write', () => ({ value: undefined }))
  on('process.run', () => ({
    value: { exitCode: 0, stdout: JSON.stringify(BAKED), stderr: '', isStdoutTruncated: false, isStderrTruncated: false },
  }))
  on('command.register', () => ({ value: {} as never }))
  on('session.start', ($, e) => ({ cwd: e.cwd }))
  on('session.id', () => ({ value: 'sess-2' }))
  // The engine's own band: an empty box.
  on('ui.render', ($, e) => { const { Box } = $.ui.resolve(e); return <Box /> })

  await $.session.start({ cwd: '/tmp', surface: 'desktop', isInteractive: true })
  const ui = await $.ui.mount({ plugin: 'codex-pet', surface: 'desktop', ...BAND })
  expect(await ui.find({ type: 'Svg' })).toBeUndefined()
  await ui.unmount()
})
