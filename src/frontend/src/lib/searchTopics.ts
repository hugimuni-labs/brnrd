export type SearchTopicSection = {
	heading: string;
	paragraphs: string[];
	bullets?: string[];
};

export type SearchTopic = {
	slug: string;
	title: string;
	description: string;
	kicker: string;
	lede: string;
	sections: SearchTopicSection[];
};

export const SEARCH_TOPICS: SearchTopic[] = [
	{
		slug: 'what-is-an-ai-harness',
		title: 'What is an AI harness?',
		description:
			'An AI harness is the execution layer around a model: tools, repository access, context, permissions, loops, and receipts that turn inference into useful work.',
		kicker: 'concept',
		lede: 'A model can generate tokens. A harness gives those tokens somewhere to act. In coding systems, the harness is the runtime that lets a model inspect a repository, edit files, run commands, call tools, keep a task loop moving, and return evidence of what happened.',
		sections: [
			{
				heading: 'Model, agent, harness: three different layers',
				paragraphs: [
					'A model is the underlying inference system. An agent is the goal-directed behavior produced when a model can observe, decide, act, and iterate. A harness is the software environment that makes those actions concrete and bounded.',
					'CLI coding agents such as Codex CLI and Claude Code therefore behave as harnesses as well as agents: they package model access together with shell execution, repository context, permissions, tool protocols, and an interaction loop.'
				],
				bullets: [
					'model: generates and reasons over tokens',
					'agent: pursues a task across multiple observations and actions',
					'harness: supplies tools, environment, policy, context and execution mechanics'
				]
			},
			{
				heading: 'Why the distinction matters',
				paragraphs: [
					'If you treat the model as the whole product, changing models looks like changing systems. If you separate the harness, the model becomes one component inside a larger execution topology. That makes portability, local execution, auditability and orchestration easier to reason about.',
					'brnrd sits one layer above individual coding harnesses. The resident keeps continuity and routing while bounded work still runs through the agent CLI already installed on the user’s machine.'
				]
			}
		]
	},
	{
		slug: 'ai-harness-vs-coding-agent',
		title: 'AI harness vs coding agent',
		description:
			'A coding agent describes goal-directed behavior; an AI harness describes the execution environment that gives the agent tools, context, permissions and a loop.',
		kicker: 'concept',
		lede: 'The terms overlap because modern coding products often ship both at once. The useful distinction is behavioral versus infrastructural: “agent” describes what the system does, while “harness” describes the machinery that lets it do it.',
		sections: [
			{
				heading: 'When “agent” is the better word',
				paragraphs: [
					'Use agent when you are talking about delegated intent: fix this bug, investigate that failure, review this pull request, or keep working until tests pass. The emphasis is autonomy across a sequence of decisions.',
					'Two products can expose very different harnesses while still presenting a similar coding-agent experience.'
				]
			},
			{
				heading: 'When “harness” is the better word',
				paragraphs: [
					'Use harness when the important question is how the work is executed: which tools exist, where commands run, what context is loaded, what approvals are required, how files are changed, and what evidence returns.',
					'That distinction becomes especially useful when composing systems. brnrd does not try to replace every coding harness with another one; it coordinates persistent identity, project context and remote reach above harnesses such as Codex CLI and Claude Code.'
				]
			}
		]
	},
	{
		slug: 'codex-cli-vs-claude-code',
		title: 'Codex CLI vs Claude Code: think in harnesses, not winners',
		description:
			'Codex CLI and Claude Code are both coding-agent harnesses. Compare them by execution model, tool behavior, context, policy and fit rather than treating one as a universal winner.',
		kicker: 'comparison',
		lede: 'Codex CLI and Claude Code occupy the same broad category: model-backed coding harnesses that can inspect a project, edit files, run commands and iterate on a task. The more durable architectural question is not which name wins, but how tightly your workflow depends on either one.',
		sections: [
			{
				heading: 'Compare the harness contract',
				paragraphs: [
					'For serious use, compare the surfaces around the model: how repository instructions are discovered, how approvals work, which tools are exposed, how sessions resume, how shell commands are represented, and how well the agent fits your existing development environment.',
					'Model quality matters, but the harness determines much of the day-to-day ergonomics and operational safety.'
				]
			},
			{
				heading: 'Avoid turning a workflow into a vendor-shaped room',
				paragraphs: [
					'If project state, routing and remote control live entirely inside one CLI’s session format, switching harnesses becomes expensive. A harness-agnostic layer keeps higher-level continuity outside any one provider.',
					'brnrd follows that topology: the resident owns durable continuity and dispatch, while the selected coding harness remains the bounded execution engine on the local machine.'
				]
			}
		]
	},
	{
		slug: 'persistent-coding-agents',
		title: 'Persistent coding agents without a permanently running model',
		description:
			'Persistence does not require keeping an LLM process alive. Durable state can preserve project knowledge, unfinished work, routing and receipts between bounded agent runs.',
		kicker: 'architecture',
		lede: 'A coding agent is often treated as a session: start a CLI, give it context, finish a task, lose the conversational state. Persistence is a different property. It can live in durable project state while model processes wake only when there is work to do.',
		sections: [
			{
				heading: 'Persist state, not inference',
				paragraphs: [
					'Useful continuity includes decisions, pitfalls, project knowledge, unresolved bearings, previous receipts and the identity of the thread or project that asked for work. None of that requires an inference process to stay warm.',
					'A resident can sleep, wake for a bounded run, restore the relevant context, execute through a coding harness, record what happened, and go quiet again.'
				]
			},
			{
				heading: 'Why this changes the product shape',
				paragraphs: [
					'When persistence is outside the model session, the user can close a terminal without conceptually destroying the agent. Work can arrive later from another surface and still resolve to the same project-aware identity.',
					'That is the core of brnrd’s “resident” model: persistent identity and state around bounded local harness runs, rather than a chatbot tab pretending to be permanent.'
				]
			}
		]
	},
	{
		slug: 'remote-coding-agents',
		title: 'Remote coding agents without moving your repository to a remote runner',
		description:
			'Remote reach and remote execution are separate concerns. A control plane can route intent to an agent that still executes beside the repository, tools and credentials on your own machine.',
		kicker: 'architecture',
		lede: '“Use my coding agent remotely” often gets interpreted as “copy the repository into someone else’s compute.” That is one topology, but it is not the only one. The ingress can be remote while execution remains local.',
		sections: [
			{
				heading: 'Separate ingress from execution',
				paragraphs: [
					'A message, issue or review can arrive through a hosted route. A small local daemon can receive the bounded task, resolve the project, invoke the coding harness beside the real checkout, and send progress or durable results back to the originating thread.',
					'The control plane coordinates identity and routing; it does not need to become the machine doing the coding.'
				]
			},
			{
				heading: 'Why local execution can be attractive',
				paragraphs: [
					'Your machine already has the repository, build cache, credentials, test environment and paid harness subscription. Reusing that environment can reduce setup duplication and keeps the execution boundary legible.',
					'brnrd is designed around this split: hosted reach is optional, while the open-source resident engine and coding harness execute where the developer already works.'
				]
			}
		]
	},
	{
		slug: 'coding-agents-from-telegram',
		title: 'Coding agents from Telegram: messaging as ingress, not the IDE',
		description:
			'A messaging surface can be a useful ingress for coding work when tasks retain project identity, bounded execution and durable receipts instead of becoming an unstructured remote shell.',
		kicker: 'workflow',
		lede: 'Telegram is a surprisingly useful control surface for coding agents because it is already good at asynchronous threads, notifications and short steering messages. The mistake is treating the messenger itself as the development environment.',
		sections: [
			{
				heading: 'The messenger should carry intent',
				paragraphs: [
					'A good remote-agent flow lets you ask for a bounded outcome, receive progress, steer when necessary and get a receipt such as a commit, pull request, issue update or concise result. The repository operations still happen in the development environment.',
					'This keeps the interaction lightweight without flattening the engineering work into chat history.'
				]
			},
			{
				heading: 'Project identity is the hard part',
				paragraphs: [
					'The system must know which repository, branch policy, environment and durable context a message belongs to. Otherwise remote messaging becomes a dangerous generic shell with ambiguous state.',
					'brnrd routes messaging ingress through a persistent resident that resolves project context before dispatching work to the local coding harness.'
				]
			}
		]
	},
	{
		slug: 'agent-orchestration',
		title: 'Agent orchestration above coding harnesses',
		description:
			'Agent orchestration coordinates identity, routing, concurrency, context and receipts across bounded coding-agent runs without requiring a new universal execution harness.',
		kicker: 'architecture',
		lede: 'Orchestration becomes useful when one agent session is no longer the whole system. Work may arrive from several surfaces, target several repositories, spawn bounded workers, require different harnesses, and need to return to the thread that asked.',
		sections: [
			{
				heading: 'What belongs in the orchestration layer',
				paragraphs: [
					'The orchestration layer should own the concerns that survive any individual run: persistent identity, project resolution, durable context, routing, concurrency policy, scheduling, steering and receipts.',
					'The execution harness should remain good at the bounded job it already knows how to do: inspect the checkout, use tools, edit files, run tests and reason about the task.'
				]
			},
			{
				heading: 'Harness-agnostic does not mean lowest-common-denominator',
				paragraphs: [
					'A useful orchestrator can expose a small common contract while still allowing provider-specific strengths underneath. The goal is not to erase Codex, Claude Code or future harnesses into identical boxes; it is to stop the rest of the system from being trapped inside one of them.',
					'brnrd calls the persistent coordinating identity a resident and bounded delegated workers strands. The vocabulary is unusual; the separation of responsibilities is conventional systems design.'
				]
			}
		]
	}
];

export function searchTopicBySlug(slug: string): SearchTopic | undefined {
	return SEARCH_TOPICS.find((topic) => topic.slug === slug);
}
