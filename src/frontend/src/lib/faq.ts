// The FAQ's questions and answers (brnrd-self threads/inbox/faq.md, 2026-11-16).
// Answers are held to what the product does today. Not to be said here: that
// it works while the computer is off, that it runs in the cloud, that it never forgets, that it needs no
// looking after, any count of users, or a price figure (the pricing page owns
// that).
export type FaqEntry = { id: string; question: string; answer: string; alias?: string };

export const FAQ: FaqEntry[] = [
	{
		id: 'what-is-brnrd',
		question: 'What is brnrd?',
		answer:
			'An AI assistant that lives on your own computer and stays the same one from day to day. You hand it work in plain words; it keeps track of what you asked, does it, and comes back with the result for you to accept or send back. Its memory is a folder you own.'
	},
	{
		id: 'versus-others',
		question: 'How is it different from OpenClaw, Hermes or Mastra?',
		answer:
			'Those give you an agent and many ways to extend it. brnrd is built around one thing: an assistant that is answerable to you. It keeps track of what you asked, shows you what it did, and a result is yours to accept or send back. It runs on the subscriptions you already pay for, and its memory is a folder you own.'
	},
	{
		id: 'why-so-small',
		question: 'Why is the subscription so small?',
		answer:
			'Because it does not pay for the AI. The thinking runs on the AI coding subscription you already have, such as Claude Code or Codex. Ours pays only for the part that runs on our side: reaching your assistant from anywhere.'
	},
	{
		id: 'more-subscription',
		question: 'Does it use a lot more of my AI subscription?',
		answer:
			"Per session, hardly: each time it starts it reads who it is and where your work stands, about 8 thousand tokens, which on our own install was a little over one percent of a week's use. What changes is the hours. A plan's allowance is gone at the end of the week whether you used it or not. brnrd works through the hours you are away, so more of what you already pay for turns into finished work, and it paces that across the week so you do not hit the limit on Tuesday."
	},
	{
		id: 'when-away',
		question: 'Does it run when I am away?',
		answer:
			'Yes, as long as the computer it lives on is on. You hand it work, leave, and write to it from your phone; it keeps going and tells you when something is ready or when it needs a decision from you.'
	},
	{
		id: 'laptop-open',
		alias: 'computer-off',
		question: 'Do I really need to keep my laptop open?',
		answer:
			'You need one computer that stays on while it works: a desktop, a small always-on machine, or a laptop that is not asleep. If the computer goes to sleep in the middle of a piece of work, that work can be cut off; what you sent while it slept is picked up when it wakes.'
	},
	{
		id: 'in-the-cloud',
		question: 'Does it run in the cloud?',
		answer:
			'No. It runs on your computer, with your files and your tools. brnrd.dev only carries messages between you and it, and shows you its board.'
	},
	{
		id: 'different-projects',
		question: 'Does it really work on different projects?',
		answer:
			'Yes. Each piece of work gets its own thread and can live in its own repository, with its own checkout and its own record of what is left.'
	},
	{
		id: 'asks-questions',
		question: 'Does it ask questions when something is unclear?',
		answer:
			'Yes. When it needs a decision that is yours, it asks, with its own recommendation, and that piece of work waits for your answer while the rest goes on.'
	},
	{
		id: 'how-is-it-different',
		question: 'How is it different from ChatGPT or Claude?',
		answer:
			'Those are the minds. brnrd is what stays when a conversation ends: what it knows about your work, what it still owes you, how you like things done. It uses those models to think, through the subscriptions you already pay for, and it can move between them.'
	},
	{
		id: 'what-do-i-need',
		question: 'What do I need to run it?',
		answer:
			'A Mac or a Linux computer, a GitHub account, and a subscription to an AI coding tool such as Claude Code or Codex. Today you install it with one line in a terminal. We know that is a hurdle and we are working on it.'
	},
	{
		id: 'where-is-my-data',
		question: 'Where does my data live?',
		answer:
			'On your computer and in your own GitHub repository. If you use brnrd.dev to reach it from your phone, the work you choose to show there is sent to our server so it can be displayed; you can use brnrd without that.'
	},
	{
		id: 'from-my-phone',
		question: 'Can I talk to it from my phone?',
		answer:
			'Yes. Through brnrd.dev and its Telegram connection, which is the paid part, or through a Telegram bot of your own, which is free and takes a little setting up.'
	},
	{
		id: 'what-does-it-cost',
		question: 'What does it cost?',
		answer:
			'The program is free and open source. Reaching it from anywhere through brnrd.dev is a subscription; the price is on the pricing page. The AI models are paid by you, to their makers, through the subscriptions you already have.'
	},
	{
		id: 'can-i-change-it',
		question: 'Can I change it?',
		answer:
			'Yes. brnrd is open source. An install made from the source code is yours to change, by you or by your assistant; when we publish a new version, your assistant decides when to take it in, and if a change stops it from starting, it goes back by itself to the last version that worked. We are making that the only kind of install.'
	},
	{
		id: 'is-it-safe',
		question: 'Is it safe to let it act for me?',
		answer:
			'It works with your rights on your computer, so treat it like a capable new colleague: start it on work where a mistake is cheap. It is told to act only within what you have allowed and to propose, not act, when a step goes beyond it; a few of the most damaging acts, such as publishing a release, are also stopped outright until you say yes. It can still make mistakes.'
	},
	{
		id: 'who-makes-it',
		question: 'Who makes it?',
		answer:
			'HugiMuni, a small company in Arles, France. The code is public under the MIT licence at github.com/hugimuni-labs/brnrd.'
	}
];
