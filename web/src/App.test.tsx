import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { App } from './App'

describe('App', () => {
  it('identifies the platform', () => {
    render(<App />)

    expect(screen.getByRole('heading', { name: 'E.C.H.O' })).toBeInTheDocument()
    expect(screen.getByText('Erez Compute Host Operations')).toBeInTheDocument()
  })
})

