import { screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

describe('application entrypoint', () => {
  beforeEach(() => {
    vi.resetModules()
    document.body.innerHTML = '<div id="root"></div>'
  })

  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('mounts the complete application into the root element', async () => {
    await import('./main')

    expect(
      await screen.findByRole('heading', { name: 'E.C.H.O' }),
    ).toBeInTheDocument()
    expect(screen.getByText('Erez Compute Host Operations')).toBeInTheDocument()
    expect(screen.getByText('Control-plane interface scaffold')).toBeInTheDocument()
  })

  it('fails clearly when the root element is missing', async () => {
    document.body.innerHTML = ''

    await expect(import('./main')).rejects.toThrow('root element is missing')
  })
})
