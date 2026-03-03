import FaqItem from '@/components/FaqItem'

const faqs = [
    {
        question: 'Hvad er Abosammenligner?',
        answer: 'Abosammenligner er et værktøj der hjælper dig med at finde de bedste tilbud på tech-produkter i forbindelse med dit mobilabonnement.'
    },
    {
        question: 'Hvordan fungerer det?',
        answer: 'Abosammenligner samler data fra forskellige udbydere og præsenterer det på en overskuelig måde, så du nemt kan sammenligne tilbud. Både gode og dårlige tilbud bliver vist så du selv kan få et overblik og træffe en informeret beslutning.'
    },
    {
        question: 'Er det gratis at bruge?',
        answer: 'Ja, Abosammenligner er helt gratis at bruge.'
    },
    {
        question: 'Hvordan kører Abosammenligner så rundt?',
        answer: 'Abosammenligner får et lille beløb af abonnementudbyderen hvis du klikker på et tilbud og køber det. Det er samme som metode som kendte sider som Pricerunner og Ønskeskyen bruger. Det betyder at det er gratis for dig at bruge, og at Abosammenligner kan fortsætte med at være gratis i fremtiden.'
    }
]

export default function Faq() {
    return (
        <main className="flex flex-col items-center justify-center-safe min-h-screen px-4 text-center">
            <h2 className="text-2xl font-bold mb-4">Ofte stillede spørgsmål</h2>
            <div className="w-full max-w-prose">
                {faqs.map((faq) => (
                    <FaqItem key={faq.question} question={faq.question} answer={faq.answer} />
                ))}
            </div>
        </main>
    )
}