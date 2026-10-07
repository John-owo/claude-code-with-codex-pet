import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { Mood, PetInfo, Status } from '../types'

type Strip = { frames: string[]; durations: number[]; w: number; h: number }
type Baked = {
  ok: boolean
  error?: string
  pet?: PetInfo
  available: { id: string; displayName: string }[]
  states?: Record<string, Strip>
  /** Set when Codex's selected pet has no local spritesheet and another was used. */
  note?: string | null
}

const mood = atom({ plugin: 'codex-pet', key: 'mood' } as const, 'idle' as Mood)
const pet = atom({ plugin: 'codex-pet', key: 'pet' } as const, null)
const bakedAt = atom({ plugin: 'codex-pet', key: 'bakedAt' } as const, 0)
const error = atom({ plugin: 'codex-pet', key: 'error' } as const, null)
const isHidden = atom({ plugin: 'codex-pet', key: 'isHidden' } as const, false)
const frame = atom({ plugin: 'codex-pet', key: 'frame' } as const, 0)

const STORE_PET = 'pet'
const STORE_HIDDEN = 'hidden'

const LABELS: Record<Mood, string> = {
  idle: '待命中',
  running: '工作中…',
  review: '看資料中…',
  waiting: '等你回覆',
  failed: '出錯了',
  jumping: '完成！',
  waving: '嗨！',
}

/** Codex's own state names; the atlas rows the bake script emits. */
const SPRITE: Record<Mood, string> = {
  idle: 'idle',
  running: 'running',
  review: 'review',
  waiting: 'waiting',
  failed: 'failed',
  jumping: 'jumping',
  waving: 'waving',
}

const LOOKING = new Set(['Read', 'Grep', 'Glob', 'WebFetch', 'WebSearch', 'LS'])
const ASKING = new Set(['AskUserQuestion', 'ExitPlanMode'])

/**
 * One frame as a static SVG. The desktop draws a non-interactive Svg as an
 * image, which shows an embedded raster but runs no SMIL, so the mod's own
 * timer steps the frames.
 */
export function frameSvg(href: string, w: number, h: number): string {
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}">` +
    `<image href="${href}" x="0" y="0" width="${w}" height="${h}"/></svg>`
  )
}

// Sprites are large; they live in the module and `bakedAt` tells drawings they changed.
let strips: Record<string, Strip> = {}
let ticker: { cancel: () => void } | undefined
let heartbeat: { cancel: () => void } | undefined
let petDir = '' // ~/.codex-pet, the folder the desktop pet and this mod share
let shared = '' // ~/.codex-pet/sessions/cc-<session id>.json: the desktop pet's view of this session
let sessionId = ''
/** What the desktop pet's card for this session says, in the Codex pet's statuses. */
let activity: { status: Status; action: string; statusAt: number; firstPrompt: string; cwd: string } = {
  status: 'idle', action: '', statusAt: 0, firstPrompt: '', cwd: '',
}
let edited = new Set<string>()
let available: Baked['available'] = []
let inTurn = false
let isAsking = false
let settle: { cancel: () => void } | undefined
let config: { python: string; pet: string; overlay: boolean } = { python: '', pet: '', overlay: true }

function base(): Mood {
  return isAsking ? 'waiting' : inTurn ? 'running' : 'idle'
}

/** Sets the mood and tells the desktop overlay (shared with Codex) about it. */
async function moodTo($: EngineInterface, next: Mood) {
  await update($, mood, () => next)
  await publish($)
}

/** Moves this session's card on the desktop pet to `status`, saying what it is doing. */
async function report($: EngineInterface, status: Status, action = activity.action) {
  const changed = status !== activity.status
  activity = { ...activity, status, action, statusAt: changed ? Date.now() : activity.statusAt }
  await publish($)
}

async function publish($: EngineInterface, ended = false) {
  if (!shared) return
  const current = await read($, pet)
  const state = {
    agent: 'cc',
    id: sessionId,
    cwd: activity.cwd,
    firstPrompt: activity.firstPrompt,
    status: ended ? 'ended' : activity.status,
    action: activity.action,
    statusAt: activity.statusAt,
    mood: await read($, mood),
    petId: current?.id ?? null,
    updatedAt: Date.now(),
  }
  try {
    await $.fs.write(shared, JSON.stringify(state))
  } catch {
    // The overlay is optional; the band above the prompt still shows the pet.
  }
}

/** Starts the desktop pet unless one is running (it keeps a single instance itself). */
async function launchOverlay($: EngineInterface) {
  const script = `${$.plugin.root}/overlay/pet_overlay.py`
  const pythonw = config.python ? config.python.replace(/python(\.exe)?$/i, 'pythonw$1') : ''
  const argv = pythonw ? [pythonw, script] : ['pyw', '-3', script]
  // `cmd /c start` leaves the pet holding run()'s output pipes, so run() waits until timeout even
  // though the pet opened; Start-Process detaches it and returns at once.
  const ps = (s: string) => `'${s.replace(/'/g, "''")}'`
  const command = `Start-Process ${ps(argv[0]!)} -ArgumentList ${argv.slice(1).map(a => ps(/\s/.test(a) ? `"${a}"` : a)).join(',')}`
  try {
    await $.process.run(['powershell', '-NoProfile', '-Command', command], { timeoutMs: 15_000 })
  } catch (err) {
    $.ui.toast(`codex-pet: 桌寵沒開起來 (${String(err)})`)
  }
}

async function overlayOn($: EngineInterface) {
  await launchOverlay($)
  await publish($)
  return { text: '桌寵已開啟（已經開著就不會重複開）。/pet-off 可以關掉。' }
}

async function overlayOff($: EngineInterface) {
  if (!petDir) return { text: '找不到 ~/.codex-pet，沒辦法通知桌寵。' }
  // The desktop pet checks for this file twice a second and closes when it appears.
  await $.fs.write(`${petDir}/takeover`, 'off')
  return { text: '已通知桌寵關閉。之後開新的 CC 對話會再自動打開，/pet-on 可以手動叫回來。' }
}

async function setMood($: EngineInterface, next: Mood) {
  settle?.cancel()
  settle = undefined
  await moodTo($, next)
}

/** A short reaction, then back to whatever CC is doing. */
async function flash($: EngineInterface, next: Mood, ms: number) {
  await setMood($, next)
  settle = $.clock.after(ms, () => {
    settle = undefined
    void moodTo($, base()).catch(() => undefined)
  })
}

const base_ = (path: string) => path.split(/[\\/]/).pop() ?? path

/** A few words on what a tool call does, for the session's card. */
function describe(tool: string, input: Record<string, unknown>): string {
  const text = (key: string) => (typeof input[key] === 'string' ? (input[key] as string) : '')
  switch (tool) {
    case 'Bash':
    case 'PowerShell':
      return `執行 ${text('description') || text('command').split('\n')[0]!.slice(0, 40)}`
    case 'Edit':
    case 'Write':
    case 'NotebookEdit':
      return `編輯 ${base_(text('file_path') || text('notebook_path'))}`
    case 'Read':
      return `讀 ${base_(text('file_path'))}`
    case 'Grep':
    case 'Glob':
      return `搜尋程式碼 ${text('pattern').slice(0, 24)}`
    case 'WebSearch':
      return `搜尋「${text('query').slice(0, 24)}」`
    case 'WebFetch':
      return '讀網頁'
    case 'Agent':
    case 'Task':
      return `派出子代理：${text('description').slice(0, 30)}`
    default:
      return tool.startsWith('mcp__') ? `呼叫 ${tool.split('__').pop()}` : `使用 ${tool}`
  }
}

function currentStrip(now: Mood): Strip | undefined {
  return strips[SPRITE[now]] ?? strips.idle
}

/** Advances `frame` on the current mood's own frame timings, for as long as the module lives. */
async function step($: EngineInterface) {
  const strip = currentStrip(await read($, mood))
  let wait = 250
  if (strip && !(await read($, isHidden))) {
    const next = ((await read($, frame)) + 1) % strip.frames.length
    await update($, frame, () => next)
    wait = strip.durations[next] ?? 150
  }
  ticker = $.clock.after(wait, () => void step($).catch(() => undefined))
}

function pythons(): string[][] {
  return [...(config.python ? [[config.python]] : []), ['py', '-3'], ['python3'], ['python']]
}

async function bake($: EngineInterface) {
  const chosen = ((await $.store.get(STORE_PET)) as string | undefined) || config.pet || 'auto'
  const script = `${$.plugin.root}/bake/bake_pet.py`
  let lastError = 'no Python found'

  for (const argv of pythons()) {
    let out: Baked
    try {
      const ran = await $.process.run([...argv, script, '--pet', chosen], { timeoutMs: 60_000 })
      if (ran.exitCode !== 0) {
        lastError = `${argv.join(' ')}: ${ran.stderr.trim().split('\n').pop() ?? 'failed'}`
        continue
      }
      out = JSON.parse(ran.stdout) as Baked
    } catch (err) {
      lastError = `${argv.join(' ')}: ${String(err)}`
      continue
    }
    available = out.available
    if (!out.ok || !out.states || !out.pet) {
      lastError = out.error ?? 'bake failed'
      if (lastError.includes('Pillow')) continue
      break
    }
    const baked = out.pet
    if (out.note) $.ui.toast(out.note)
    strips = out.states
    await update($, pet, () => baked)
    await update($, error, () => null)
    await update($, bakedAt, () => Date.now())
    await publish($)
    return
  }
  await update($, error, () => lastError)
}

export const register: Register = (on, options) => {
  const text = (v: unknown) => (typeof v === 'string' ? v.trim() : '')
  config = { python: text(options.python), pet: text(options.pet), overlay: options.overlay !== false }
  strips = {}
  ticker?.cancel()
  ticker = undefined
  heartbeat?.cancel()
  heartbeat = undefined
  inTurn = false
  isAsking = false
  settle = undefined

  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'pet',
      description: 'Codex pet: /pet [list | <id> | codex | hide | show | reload]',
    })
    await $.command.register({ name: 'pet-on', description: 'Codex pet: 打開桌寵' })
    await $.command.register({ name: 'pet-off', description: 'Codex pet: 關掉桌寵' })
    // With the desktop pet on, the band stays off unless /pet show turned it on.
    const stored = await $.store.get(STORE_HIDDEN)
    const hidden = typeof stored === 'boolean' ? stored : config.overlay
    await update($, isHidden, () => hidden)
    const home = (await $.env.get('USERPROFILE')) ?? (await $.env.get('HOME'))
    sessionId = await $.session.id()
    petDir = home ? `${home}/.codex-pet` : ''
    shared = petDir ? `${petDir}/sessions/cc-${sessionId}.json` : ''
    const firstPrompt = (await $.store.get(`firstPrompt:${sessionId}`)) as string | undefined
    activity = { status: 'idle', action: '', statusAt: Date.now(), firstPrompt: firstPrompt ?? '', cwd: e.cwd }
    edited = new Set()
    await bake($)
    if (config.overlay) await launchOverlay($)
    await flash($, 'waving', 2500)
    if (!ticker) void step($).catch(() => undefined)
    heartbeat ??= $.clock.every(30_000, () => void publish($).catch(() => undefined))

    return next(e)
  })

  on('command.run', { command: 'pet' }, async ($, e) => {
    const arg = e.args.trim()

    if (arg === 'hide' || arg === 'show') {
      await $.store.set(STORE_HIDDEN, arg === 'hide')
      await update($, isHidden, () => arg === 'hide')
      return { text: arg === 'hide' ? '寵物收起來了。' : '寵物出來了。' }
    }
    if (arg === '' || arg === 'list') {
      const current = await read($, pet)
      const rows = available.map(p => `${p.id === current?.id ? '▶' : ' '} ${p.id}  (${p.displayName})`)
      return {
        text:
          `Codex 寵物（~/.codex/pets）：\n${rows.join('\n') || '  (沒有找到)'}\n\n` +
          '/pet <id> 切換 · /pet codex 跟隨 Codex 的選擇 · /pet-on · /pet-off 開關桌寵 · /pet hide|show · /pet reload',
      }
    }
    if (arg === 'overlay' || arg === 'overlay on') return overlayOn($)
    if (arg === 'overlay off') return overlayOff($)
    if (arg === 'reload') {
      await bake($)
      const failed = await read($, error)
      return { text: failed ? `重新載入失敗：${failed}` : '已重新載入。' }
    }
    if (arg === 'codex') {
      await $.store.delete(STORE_PET)
    } else {
      if (!available.some(p => p.id === arg)) {
        return { text: `找不到寵物「${arg}」。用 /pet list 看看有哪些。` }
      }
      await $.store.set(STORE_PET, arg)
    }
    await bake($)
    await flash($, 'waving', 2500)
    const now = await read($, pet)
    return { text: now ? `現在的寵物：${now.displayName}` : `切換失敗：${await read($, error)}` }
  })

  on('command.run', { command: 'pet-on' }, async $ => overlayOn($))
  on('command.run', { command: 'pet-off' }, async $ => overlayOff($))

  on('session.end', async ($, e, next) => {
    await update($, mood, () => 'idle')
    await publish($, true)
    return next(e)
  })

  on('turn.start', async ($, e, next) => {
    inTurn = true
    isAsking = false
    edited = new Set()
    if (!activity.firstPrompt && e.text.trim()) {
      activity.firstPrompt = e.text.trim().split('\n')[0]!.slice(0, 60)
      await $.store.set(`firstPrompt:${sessionId}`, activity.firstPrompt)
    }
    await report($, 'running', '思考中')
    await setMood($, 'running')
    return next(e)
  })

  on('tool.call', async ($, e, next) => {
    const asking = ASKING.has(e.tool)
    if (asking) isAsking = true
    const input = e as unknown as Record<string, unknown>
    if (['Edit', 'Write', 'NotebookEdit'].includes(e.tool)) {
      edited.add(String(input.file_path ?? input.notebook_path ?? ''))
    }
    await report($, asking ? 'waiting' : 'running', asking ? '等你回答' : describe(e.tool, input))
    if (!settle) await moodTo($, (asking ? 'waiting' : LOOKING.has(e.tool) ? 'review' : 'running'))

    const ran = await next(e)

    if (asking) {
      isAsking = false
      await report($, 'running')
    }
    if (ran.deny === undefined && ran.isError === true) {
      await flash($, 'failed', 2000)
    } else if (!settle) {
      await moodTo($, base())
    }
    return ran
  })

  on('classic.PermissionRequest', async ($, e, next) => {
    isAsking = true
    const doing = activity.action
    await report($, 'waiting', `等你核准 ${e.tool_name}`)
    await setMood($, 'waiting')
    const answer = await next(e)
    isAsking = false
    await report($, 'running', doing)
    await moodTo($, base())
    return answer
  })

  on('turn.complete', async ($, e, next) => {
    inTurn = false
    isAsking = false
    const said = e.answer.trim().split('\n').find(line => line.trim())?.replace(/[*#`>]/g, '').trim() ?? ''
    if (e.reason === 'answer') {
      await report($, 'ready', said.slice(0, 60) || (edited.size ? `編輯了 ${edited.size} 個檔案` : '完成'))
    } else if (e.reason === 'aborted') {
      await report($, 'idle', '')
    } else {
      await report($, 'blocked', e.reason === 'refusal' ? '拒絕了這個要求' : 'API 錯誤')
    }
    if (e.reason === 'answer') await flash($, 'jumping', 2500)
    else if (e.reason === 'aborted') await setMood($, 'idle')
    else await flash($, 'failed', 4000)
    return next(e)
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (await read($, isHidden)) return next(e)
    const current = await read($, pet)
    const failed = await read($, error)
    await read($, bakedAt)
    const now = await read($, mood)
    const { Box, Text } = $.ui.resolve(e)

    if (!current) {
      return failed ? (
        <Box>
          <Text dimColor>codex-pet: {failed}</Text>
        </Box>
      ) : (
        next(e)
      )
    }

    const label = `${current.displayName} · ${LABELS[now]}`

    if (e.surface === 'desktop') {
      const { Svg } = $.ui.resolve(e)
      const strip = currentStrip(now)
      const at = await read($, frame)
      const href = strip?.frames[at % strip.frames.length]
      return (
        <Box flexDirection="row" alignItems="flex-end">
          {strip && href && (
            <Svg source={frameSvg(href, strip.w, strip.h)} alt={label} width={66} height={72} />
          )}
          <Text dimColor> {label}</Text>
        </Box>
      )
    }

    return (
      <Box>
        <Text dimColor>🐾 {label}</Text>
      </Box>
    )
  })
}
