export type Mood =
  | 'idle'
  | 'running'
  | 'review'
  | 'waiting'
  | 'failed'
  | 'jumping'
  | 'waving'

/** A session's status on the desktop pet, as the Codex pet names them. */
export type Status = 'idle' | 'running' | 'waiting' | 'blocked' | 'ready'

export type PetInfo = { id: string; displayName: string; followsCodex: boolean }

declare module 'claude-code' {
  interface PluginState {
    'codex-pet': {
      mood: Mood
      pet: PetInfo | null
      /** Bumped when the module's sprite cache is (re)filled, so the band redraws. */
      bakedAt: number
      error: string | null
      isHidden: boolean
      /** Index into the current mood's frames; the mod's timer advances it. */
      frame: number
    }
  }
}
