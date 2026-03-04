import type { Offer } from '@/types/offer'
import Tooltip from './Tooltip'

interface OfferCardProps {
    offer: Offer
}

export default function OfferCard({ offer }: OfferCardProps) {
    const base = offer.market_price ?? offer.price_without_subscription
    const hasMinCost = offer.min_cost_6_months != null && offer.min_cost_6_months > 0
    const saved = hasMinCost ? base - offer.min_cost_6_months : null
    const isFallback = offer.market_price === null

    return (
        <div className="border border-[#334155] rounded-xl p-4 flex flex-row items-start gap-2 bg-[#2a3340]">
            {offer.image_url && (
                <img
                    src={offer.image_url}
                    alt={offer.product_name}
                    className="w-30 h-30 object-contain flex-shrink-0 self-center"
                />
            )}

            <div className="flex flex-col gap-1 flex-1 min-w-0">
                <p className="text-[11px] text-[#7d8fa0] m-0">{offer.provider}</p>
                <h2 className="text-[15px] leading-snug text-[#cdd6e0] m-0 font-semibold">{offer.product_name}</h2>

                <p className="text-sm text-[#cdd6e0] m-0">
                    <strong>{offer.price_with_subscription} kr.</strong> med abonnement
                </p>

                <hr className="border-none border-t border-[#334155] my-1" />


                <p className="text-xs text-[#7d8fa0] m-0">
                    Rabat på telefonen: {offer.discount_on_product} kr.
                </p>



                <p className="text-xs text-[#7d8fa0] m-0 flex items-center gap-1">
                    Pris uden abonnement: {offer.price_without_subscription} kr.
                    <Tooltip text="Ifølge abonnementudbyderen" />
                </p>

                <p className="text-xs text-[#7d8fa0] m-0 flex items-center gap-1">
                    {offer.market_price ? (
                        <>
                            Markedspris: {offer.market_price} kr.
                            <Tooltip text="Billigste tilbud lige nu iflg. pricerunner/prisjagt" />
                        </>
                    ) : (
                        <span className="text-[#7d8fa0] italic">Ingen markedspris fundet</span>
                    )}
                </p>

                <p className="text-xs text-[#7d8fa0] m-0">
                    Abonnementspris pr. måned:{' '}
                    {offer.subscription_price_monthly != null ? (
                        offer.subscription_price_monthly_after_promo != null ? (
                            <span>
                                {offer.subscription_price_monthly} kr.,{' '}
                                <span className="italic">derefter {offer.subscription_price_monthly_after_promo} kr.</span>
                            </span>
                        ) : (
                            <span>{offer.subscription_price_monthly} kr.</span>
                        )
                    ) : (
                        <span className="italic">Ikke oplyst</span>
                    )}
                </p>

                {offer.min_cost_6_months && (
                    <p className="text-xs text-[#7d8fa0] m-0">
                        Mindstepris i 6 mdr.:{' '}
                        <span className="underline">{offer.min_cost_6_months} kr.</span>
                    </p>
                )}
                
                <hr className="border-none border-t border-[#334155] my-1" />

                <p className="text-[13px] text-[#cdd6e0] m-0 flex items-center gap-1">
                    Penge reelt sparet efter 6 mdr.:{' '}
                    {saved != null ? (
                        <>
                            <span className={`font-bold text-[15px] ${saved > 0 ? 'text-[#4caf82]' : 'text-[#e05555]'}`}>
                                {saved} kr.
                            </span>
                            {isFallback && (
                                <Tooltip text="Ingen markedspris fundet. Resultatet er ud fra udbyderens tal." />
                            )}
                        </>
                    ) : (
                        <span className="text-[#7d8fa0] italic">Ikke tilgængelig</span>
                    )}
                </p>

                <a
                    href={offer.link}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="mt-2 px-4 py-2 rounded-lg bg-[#4a90b8] text-white text-[13px] font-bold self-start hover:bg-[#3a7aa0] transition-colors text-center"
                >
                    Gå til tilbud
                </a>
            </div>
        </div>
    )
}
