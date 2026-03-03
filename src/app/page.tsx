'use client'
import OfferCard from '@/components/OfferCard'
import ProviderFilter from '@/components/ProviderFilter'
import SortSelect from '@/components/SortSelect'
import { useOffers } from '@/hooks/useOffers'

export default function Home() {
    const { filtered, selectedProviders, setSelectedProviders, sortOrder, setSortOrder, hideNegative, setHideNegative, search, setSearch } = useOffers()

    return (
        <main className="min-h-screen bg-[#20262f] p-8">
            <div className="max-w-[1200px] mx-auto">
                <h1 className="mb-6 text-[#cdd6e0]">Mobiltelefoner med abonnement</h1>

                <div className="flex gap-4 mb-8 flex-wrap items-center">
                    <ProviderFilter selected={selectedProviders} onChange={setSelectedProviders} />
                    <SortSelect value={sortOrder} onChange={setSortOrder} />
                    <button
                        onClick={() => setHideNegative(v => !v)}
                        className={`px-4 py-2 rounded-md border text-sm transition-colors ${
                            hideNegative
                                ? 'bg-[#4a90b8] border-[#4a90b8] text-white'
                                : 'bg-[#2a3340] border-[#334155] text-[#7d8fa0] hover:border-[#4a90b8]'
                        }`}
                    >
                        {hideNegative ? 'Skjuler tilbud du ikke sparer penge på' : 'Skjul tilbud du ikke sparer penge på'}
                    </button>
                    <input
                        type="text"
                        value={search}
                        onChange={e => setSearch(e.target.value)}
                        placeholder="Søg efter telefon..."
                        className="px-4 py-2 rounded-md border border-[#334155] bg-[#2a3340] text-[#cdd6e0] text-sm placeholder-[#7d8fa0] focus:outline-none focus:border-[#4a90b8]"
                    />
                </div>

                <div className="grid grid-cols-[repeat(auto-fill,minmax(360px,1fr))] gap-6">
                    {filtered.map(offer => (
                        <OfferCard
                            key={`${offer.provider}-${offer.product_name}-${offer.link}`}
                            offer={offer}
                        />

                    ))}
                </div>
            </div>
        </main>
    )
}
