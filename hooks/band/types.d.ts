// What src/as_agreed/band.py writes to .as-agreed/runs/<session>/band.json, and the band module reads.
export type Band = {
  labels: string[]
  terms: number
  kept: number
  held: number
  changed_not_held: number
  unchecked: number
  why: string
}

declare module 'claude-code' {
  interface PluginState {
    'as-agreed': { band: Band | null }
  }
}
