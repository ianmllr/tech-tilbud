import { describe, it, expect } from 'vitest'
import { allOffers, CATEGORY_LABELS, isAllowedLink } from './offers'

describe('offers data', () => {
    it('exports known category labels', () => {
        expect(CATEGORY_LABELS.phone).toBe('Mobiler')
        expect(CATEGORY_LABELS.tablet).toBe('Tablets')
    })

    it('contains offers with required fields', () => {
        expect(allOffers.length).toBeGreaterThan(0)
        expect(allOffers.every(o => o.provider && o.product_name && o.link)).toBe(true)
    })
})

describe('isAllowedLink', () => {
    it('accepts https links to allowlisted hosts and their subdomains', () => {
        expect(isAllowedLink('https://telmore.dk/x')).toBe(true)
        expect(isAllowedLink('https://www.telmore.dk/x')).toBe(true)
        expect(isAllowedLink('https://pin.3.dk/c/123')).toBe(true)
        expect(isAllowedLink('https://go.adt284.net/t/t?a=1')).toBe(true)
    })

    it('rejects lookalike hosts, other protocols and junk', () => {
        expect(isAllowedLink('https://evil-telmore.dk/')).toBe(false)
        expect(isAllowedLink('https://telmore.dk.evil.com/')).toBe(false)
        expect(isAllowedLink('https://x3.dk/')).toBe(false)
        expect(isAllowedLink('http://www.telmore.dk/')).toBe(false)
        expect(isAllowedLink('javascript:alert(1)')).toBe(false)
        expect(isAllowedLink('/relative/path')).toBe(false)
        expect(isAllowedLink(undefined)).toBe(false)
    })

    it('keeps every offer link on an allowed host', () => {
        expect(allOffers.every(o => isAllowedLink(o.link))).toBe(true)
    })
})
