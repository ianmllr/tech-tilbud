import telmore_raw from '../../data/telmore/telmore_offers.json'
import telmore_tilgift_raw from '../../data/telmore/telmore_tilgift_offers.json'
import oister_raw from '../../data/oister/oister_offers.json'
import elgiganten_raw from '../../data/elgiganten/elgiganten_offers.json'
import cbb_raw from '../../data/cbb/cbb_offers.json'
import three_raw from '../../data/3/3_offers.json'
import yousee_raw from '../../data/yousee/yousee_offers.json'
import norlys_raw from '../../data/norlys/norlys_offers.json'
import callme_raw from '../../data/callme/callme_offers.json'
import prisjagt from '../../data/prisjagt/prisjagt_prices.json'
import pricerunner from '../../data/pricerunner/pricerunner_prices.json'
import type { Offer } from '@/types/offer'

type ElgigantenOffer = {
    link: string
    product_name?: string
    product?: string
    image_url: string
    provider?: string
    type?: string
    price_with_subscription: number
    price_without_subscription: number
    discount_on_product: number
    min_cost_6_months: number
    subscription_price_monthly: number
}

type ProviderOffer = {
    link: string
    product_name: string
    image_url: string
    type?: string
    price_with_subscription: number
    price_without_subscription: number
    discount_on_product: number
    min_cost_6_months: number
    subscription_price_monthly: number
    subscription_price_monthly_after_promo?: number | null
}

/**
 * Scraped JSON is external data whose shape TypeScript cannot verify. Going
 * through `unknown` also keeps the build working when a scrape produces an
 * empty file, which would otherwise be inferred as `never[]`.
 */
const asOffers = (data: unknown): ProviderOffer[] => data as ProviderOffer[]

const telmore = asOffers(telmore_raw)
const telmore_tilgift = asOffers(telmore_tilgift_raw)
const oister = asOffers(oister_raw)
const cbb = asOffers(cbb_raw)
const three = asOffers(three_raw)
const yousee = asOffers(yousee_raw)
const norlys = asOffers(norlys_raw)
const callme = asOffers(callme_raw)
const elgiganten = elgiganten_raw as unknown as ElgigantenOffer[]

const prisjagtLookup = prisjagt as Record<string, { market_price: number | null }>
const pricerunnerLookup = pricerunner as Record<string, { market_price: number | null }>

// providers spell names with different casing ("AirPods" vs "Airpods"), so index
// case-insensitively and keep the cheapest when keys collide
function buildPriceIndex(lookup: Record<string, { market_price: number | null }>) {
    const index = new Map<string, number>()
    for (const [name, entry] of Object.entries(lookup)) {
        const price = entry?.market_price
        if (price === null || price === undefined) continue
        const key = name.toLowerCase()
        const current = index.get(key)
        index.set(key, current === undefined ? price : Math.min(current, price))
    }
    return index
}

const prisjagtIndex = buildPriceIndex(prisjagtLookup)
const pricerunnerIndex = buildPriceIndex(pricerunnerLookup)

function lowestMarketPrice(productName: string): number | null {
    const key = productName.toLowerCase()
    const a = prisjagtIndex.get(key) ?? null
    const b = pricerunnerIndex.get(key) ?? null
    if (a !== null && b !== null) return Math.min(a, b)
    return a ?? b
}

// a market price far below the provider's own cash price means the lookup matched
// the wrong product — usually because the provider name is too vague to identify
// (Telmore lists a phone as just "Signature"). drop it rather than advertise a
// saving that isn't real; the offer is simply hidden instead
const MIN_PLAUSIBLE_MARKET_RATIO = 0.5

function plausibleMarketPrice(productName: string, cashPrice: number): number | null {
    const market = lowestMarketPrice(productName)
    if (market === null) return null
    if (cashPrice > 0 && market < cashPrice * MIN_PLAUSIBLE_MARKET_RATIO) return null
    return market
}

export const allOffers: Offer[] = [
    ...telmore.map(o => ({
        link: o.link,
        product_name: o.product_name,
        image_url: o.image_url,
        provider: 'Telmore' as const,
        type: o.type ?? 'phone',
        price_with_subscription: o.price_with_subscription,
        price_without_subscription: o.price_without_subscription,
        discount_on_product: o.discount_on_product,
        min_cost_6_months: o.min_cost_6_months,
        subscription_price_monthly: o.subscription_price_monthly,
        subscription_price_monthly_after_promo: null,
    })),
    ...telmore_tilgift.map(o => ({
        link: o.link,
        product_name: o.product_name,
        image_url: o.image_url,
        provider: 'Telmore' as const,
        type: o.type ?? 'phone',
        price_with_subscription: o.price_with_subscription,
        price_without_subscription: o.price_without_subscription,
        discount_on_product: o.discount_on_product,
        min_cost_6_months: o.min_cost_6_months,
        subscription_price_monthly: o.subscription_price_monthly,
        subscription_price_monthly_after_promo: o.subscription_price_monthly_after_promo ?? null,
    })),
    ...oister.map(o => ({
        link: o.link,
        product_name: o.product_name,
        image_url: o.image_url,
        provider: 'Oister' as const,
        type: o.type ?? 'phone',
        price_with_subscription: o.price_with_subscription,
        price_without_subscription: o.price_without_subscription,
        discount_on_product: o.discount_on_product,
        min_cost_6_months: o.min_cost_6_months,
        subscription_price_monthly: o.subscription_price_monthly,
        subscription_price_monthly_after_promo: null,
    })),
    ...elgiganten.map(o => ({
        link: o.link,
        product_name: o.product_name ?? o.product ?? '',
        image_url: o.image_url,
        provider: (o.provider as 'Elgiganten') ?? 'Elgiganten',
        type: o.type ?? 'phone',
        price_with_subscription: o.price_with_subscription,
        price_without_subscription: o.price_without_subscription,
        discount_on_product: o.discount_on_product,
        min_cost_6_months: o.min_cost_6_months,
        subscription_price_monthly: o.subscription_price_monthly,
        subscription_price_monthly_after_promo: null,
    })),
    ...cbb.map(o => ({
        link: o.link,
        product_name: o.product_name,
        image_url: o.image_url,
        provider: 'CBB' as const,
        type: o.type ?? 'phone',
        price_with_subscription: o.price_with_subscription,
        price_without_subscription: o.price_without_subscription,
        discount_on_product: o.discount_on_product,
        min_cost_6_months: o.min_cost_6_months,
        subscription_price_monthly: o.subscription_price_monthly,
        subscription_price_monthly_after_promo: o.subscription_price_monthly_after_promo ?? null,
    })),
    ...three.map(o => ({
        link: o.link,
        product_name: o.product_name,
        image_url: o.image_url,
        provider: '3' as const,
        type: o.type ?? 'phone',
        price_with_subscription: o.price_with_subscription,
        price_without_subscription: o.price_without_subscription,
        discount_on_product: o.discount_on_product,
        min_cost_6_months: o.min_cost_6_months,
        subscription_price_monthly: o.subscription_price_monthly,
        subscription_price_monthly_after_promo: null,
    })),
    ...yousee.map(o => ({
        link: o.link,
        product_name: o.product_name,
        image_url: o.image_url,
        provider: 'YouSee' as const,
        type: o.type ?? 'phone',
        price_with_subscription: o.price_with_subscription,
        price_without_subscription: o.price_without_subscription,
        discount_on_product: o.discount_on_product,
        min_cost_6_months: o.min_cost_6_months,
        subscription_price_monthly: o.subscription_price_monthly,
        subscription_price_monthly_after_promo: null,
    })),
    ...norlys.map(o => ({
        link: o.link,
        product_name: o.product_name,
        image_url: o.image_url,
        provider: 'Norlys' as const,
        type: o.type ?? 'phone',
        price_with_subscription: o.price_with_subscription,
        price_without_subscription: o.price_without_subscription,
        discount_on_product: o.discount_on_product,
        min_cost_6_months: o.min_cost_6_months,
        subscription_price_monthly: o.subscription_price_monthly,
        subscription_price_monthly_after_promo: null,
    })),
    ...callme.map(o => ({
        link: o.link,
        product_name: o.product_name,
        image_url: o.image_url,
        provider: 'CallMe' as const,
        type: o.type ?? 'phone',
        price_with_subscription: o.price_with_subscription,
        price_without_subscription: o.price_without_subscription,
        discount_on_product: o.discount_on_product,
        min_cost_6_months: o.min_cost_6_months,
        subscription_price_monthly: o.subscription_price_monthly,
        subscription_price_monthly_after_promo: null,
    })),

].map(offer => ({
    ...offer,
    market_price: plausibleMarketPrice(offer.product_name, offer.price_without_subscription),
}))

export const PROVIDERS = ['Telmore', 'Oister', 'Elgiganten', 'CBB', '3', 'YouSee', 'Norlys', 'CallMe'] as const

export const CATEGORIES = ['phone', 'tablet', 'sound', 'gaming'] as const
export type Category = typeof CATEGORIES[number]

export const CATEGORY_LABELS: Record<Category, string> = {
    phone: 'Mobiler',
    tablet: 'Tablets',
    sound: 'Lyd',
    gaming: 'Gaming',
}