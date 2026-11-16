// The FAQ's questions and answers (brnrd-self threads/inbox/faq.md, 2026-11-16).
// Answers are held to what the product does today. Not to be said here: that
// it works while the computer is off, that it never forgets, that it needs no
// looking after, any count of users, or a price figure (the pricing page owns
// that).
export type FaqEntry = { id: string; question: string; answer: string };

export const FAQ: FaqEntry[] = [
	{
		id: 'what-is-brnrd',
		question: 'What is brnrd?',
		answer:
			'An AI assistant that lives on your own computer and stays the same one from day to day. You hand it work in plain words; it keeps track of what you asked, does it, and comes back with the result for you to accept or send back. Its memory is a folder you own.'
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
		id: 'computer-off',
		question: 'Does it work when my computer is off?',
		answer:
			'No. It runs on your computer, so it works while that computer is on. Work you gave it waits and continues when the computer is back.'
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
			'Yes. Your install carries its own copy of the code, and you or your assistant may change it. When we publish a new version, your assistant decides when to take it in, and if a change stops it from starting, it goes back by itself to the last version that worked.'
	},
	{
		id: 'is-it-safe',
		question: 'Is it safe to let it act for me?',
		answer:
			'A short list of destructive acts is stopped until you say yes in a message: deleting its own memory and workspaces, force-pushing, uploading a package, publishing a release, and merging a pull request whose checks are not green. These guards catch careless mistakes, not a determined workaround, and with Codex only the merge and release guards apply. It can still make mistakes, as any assistant can; start it on work where a mistake is cheap.'
	},
	{
		id: 'who-makes-it',
		question: 'Who makes it?',
		answer:
			'HugiMuni, a small company in Arles, France. The code is public under the MIT licence at github.com/hugimuni-labs/brnrd.'
	},
	{
		id: 'is-it-a-person',
		question: 'Is the thing answering me on Telegram a person?',
		answer: 'No. It is brnrd, an AI. It says so.'
	}
];
