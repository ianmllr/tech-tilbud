import { useState, useMemo } from 'react'
import { allOffers, PROVIDERS, CATEGORIES } from '@/lib/offers'
import type { SortOrder } from '@/types/offer'

const validOffers = allOffers.filter(o => o.min_cost_6_months != null)
const GLOBAL_MIN = Math.floor(Math.min(...validOffers.map(o => o.min_cost_6_months as number)))
const GLOBAL_MAX = Math.ceil(Math.max(...validOffers.map(o => o.min_cost_6_months as number)))
const ITEMS_PER_PAGE = 50

export function useOffers() {
    const [selectedProviders, setSelectedProviders] = useState<string[]>([...PROVIDERS])
    const [selectedCategories, setSelectedCategories] = useState<string[]>([...CATEGORIES])
    const [sortOrder, setSortOrder] = useState<SortOrder>('saved_desc')
    const [hideNegative, setHideNegative] = useState(true)
    const [search, setSearch] = useState('')
    const [priceRange, setPriceRange] = useState<[number, number]>([GLOBAL_MIN, GLOBAL_MAX])
    const [page, setPage] = useState(1)

    const filtered = useMemo(() => {
        const q = search.trim().toLowerCase()
        const [lo, hi] = priceRange
        const startIndex = (page - 1) * ITEMS_PER_PAGE
        const endIndex = startIndex + ITEMS_PER_PAGE

        return allOffers
            .filter(o => selectedProviders.includes(o.provider))
            .filter(o => selectedCategories.includes(o.type))
            .filter(o => !q || o.product_name.toLowerCase().includes(q))
            .filter(o => o.market_price != null)
            .filter(o => o.price_with_subscription !== null && o.price_with_subscription !== undefined)
            .filter(o => o.min_cost_6_months == null || (o.min_cost_6_months >= lo && o.min_cost_6_months <= hi))
            .filter(o => {
                if (!hideNegative) return true
                if (o.min_cost_6_months == null) return false
                return o.market_price! - o.min_cost_6_months >= 0
            })
            .sort((a, b) => {
                if (sortOrder === 'saved_desc' || sortOrder === 'saved_asc') {
                    const aSaved = a.min_cost_6_months != null ? a.market_price! - a.min_cost_6_months : -Infinity
                    const bSaved = b.min_cost_6_months != null ? b.market_price! - b.min_cost_6_months : -Infinity
                    return sortOrder === 'saved_desc' ? bSaved - aSaved : aSaved - bSaved
                }
                if (sortOrder === 'pct_desc' || sortOrder === 'pct_asc') {
                    const aPct = a.min_cost_6_months != null && a.market_price! > 0 ? (a.market_price! - a.min_cost_6_months) / a.market_price! : -Infinity
                    const bPct = b.min_cost_6_months != null && b.market_price! > 0 ? (b.market_price! - b.min_cost_6_months) / b.market_price! : -Infinity
                    return sortOrder === 'pct_desc' ? bPct - aPct : aPct - bPct
                }
                if (sortOrder === 'market_asc' || sortOrder === 'market_desc') {
                    return sortOrder === 'market_asc' ? a.market_price! - b.market_price! : b.market_price! - a.market_price!
                }
                if (sortOrder === 'name_asc' || sortOrder === 'name_desc') {
                    const cmp = a.product_name.localeCompare(b.product_name, 'da')
                    return sortOrder === 'name_asc' ? cmp : -cmp
                }
                return sortOrder === 'asc'
                    ? Number(a.price_with_subscription) - Number(b.price_with_subscription)
                    : Number(b.price_with_subscription) - Number(a.price_with_subscription)
            })
            .slice(startIndex, endIndex)
    }, [selectedProviders, selectedCategories, sortOrder, hideNegative, search, priceRange, page])


    const totalFiltered = useMemo(() => {
        const q = search.trim().toLowerCase()
        const [lo, hi] = priceRange

        return allOffers
            .filter(o => selectedProviders.includes(o.provider))
            .filter(o => selectedCategories.includes(o.type))
            .filter(o => !q || o.product_name.toLowerCase().includes(q))
            .filter(o => o.market_price != null)
            .filter(o => o.price_with_subscription !== null && o.price_with_subscription !== undefined)
            .filter(o => o.min_cost_6_months == null || (o.min_cost_6_months >= lo && o.min_cost_6_months <= hi))
            .filter(o => {
                if (!hideNegative) return true
                if (o.min_cost_6_months == null) return false
                return o.market_price! - o.min_cost_6_months >= 0
            })
            .sort((a, b) => {
                if (sortOrder === 'saved_desc' || sortOrder === 'saved_asc') {
                    const aSaved = a.min_cost_6_months != null ? a.market_price! - a.min_cost_6_months : -Infinity
                    const bSaved = b.min_cost_6_months != null ? b.market_price! - b.min_cost_6_months : -Infinity
                    return sortOrder === 'saved_desc' ? bSaved - aSaved : aSaved - bSaved
                }
                if (sortOrder === 'pct_desc' || sortOrder === 'pct_asc') {
                    const aPct = a.min_cost_6_months != null && a.market_price! > 0 ? (a.market_price! - a.min_cost_6_months) / a.market_price! : -Infinity
                    const bPct = b.min_cost_6_months != null && b.market_price! > 0 ? (b.market_price! - b.min_cost_6_months) / b.market_price! : -Infinity
                    return sortOrder === 'pct_desc' ? bPct - aPct : aPct - bPct
                }
                if (sortOrder === 'market_asc' || sortOrder === 'market_desc') {
                    return sortOrder === 'market_asc' ? a.market_price! - b.market_price! : b.market_price! - a.market_price!
                }
                if (sortOrder === 'name_asc' || sortOrder === 'name_desc') {
                    const cmp = a.product_name.localeCompare(b.product_name, 'da')
                    return sortOrder === 'name_asc' ? cmp : -cmp
                }
                return sortOrder === 'asc'
                    ? Number(a.price_with_subscription) - Number(b.price_with_subscription)
                    : Number(b.price_with_subscription) - Number(a.price_with_subscription)
            })
    }, [selectedProviders, selectedCategories, sortOrder, hideNegative, search, priceRange])
    
    const totalPages = Math.ceil(totalFiltered.length / ITEMS_PER_PAGE)

    return { filtered, page, setPage, totalPages, selectedProviders, setSelectedProviders, selectedCategories, setSelectedCategories, sortOrder, setSortOrder, hideNegative, setHideNegative, search, setSearch, priceRange, setPriceRange, priceMin: GLOBAL_MIN, priceMax: GLOBAL_MAX }
}
