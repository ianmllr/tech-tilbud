import { PROVIDERS } from '@/lib/offers'

interface ProviderFilterProps {
    selected: string[]
    onChange: (providers: string[]) => void
}

export default function ProviderFilter({ selected, onChange }: ProviderFilterProps) {
    const allSelected = selected.length === PROVIDERS.length

    const toggleAll = () => {
        onChange(allSelected ? [] : [...PROVIDERS])
    }

    const toggleOne = (provider: string) => {
        if (selected.includes(provider)) {
            onChange(selected.filter(p => p !== provider))
        } else {
            onChange([...selected, provider])
        }
    }

    return (
        <div className="flex gap-2 flex-wrap">
            <button
                onClick={toggleAll}
                className={`px-4 py-2 rounded-md border text-sm cursor-pointer transition-colors
                    ${allSelected
                        ? 'bg-[#4a90b8] border-[#4a90b8] text-white'
                        : 'bg-[#2a3340] border-[#334155] text-[#7d8fa0] hover:border-[#4a90b8]'}`}
            >
                Alle
            </button>
            {PROVIDERS.map(p => (
                <button
                    key={p}
                    onClick={() => toggleOne(p)}
                    className={`px-4 py-2 rounded-md border text-sm cursor-pointer transition-colors
                        ${selected.includes(p)
                            ? 'bg-[#4a90b8] border-[#4a90b8] text-white'
                            : 'bg-[#2a3340] border-[#334155] text-[#7d8fa0] hover:border-[#4a90b8]'}`}
                >
                    {p}
                </button>
            ))}
        </div>
    )
}
